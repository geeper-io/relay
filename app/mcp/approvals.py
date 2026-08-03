"""Durable, argument-bound MCP approval lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import AuthorizationError
from app.db.models import AuditLog, MCPApproval, MCPApprovalGrantOffer


def arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_approval(
    db: AsyncSession,
    *,
    user_id: str,
    team_id: str | None,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    purpose: str | None,
    policy_version: str,
    ttl_seconds: int,
    request_id: str,
    grant_template: dict[str, Any] | None = None,
) -> MCPApproval:
    now = datetime.now(timezone.utc)
    approval = MCPApproval(
        id=str(uuid.uuid4()),
        user_id=user_id,
        team_id=team_id,
        server_name=server,
        tool_name=tool,
        arguments_hash=arguments_hash(arguments),
        arguments=arguments,
        purpose=purpose,
        policy_version=policy_version,
        status="pending",
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    db.add(approval)
    if grant_template:
        db.add(MCPApprovalGrantOffer(approval_id=approval.id, template=grant_template))
    db.add(
        _audit(
            request_id,
            user_id,
            "mcp.approval.requested",
            f"{server}/{tool}",
            {"approval_id": approval.id, "team_id": team_id, "policy_version": policy_version},
        )
    )
    await db.commit()
    return approval


async def get_approval(db: AsyncSession, approval_id: str) -> MCPApproval | None:
    return await db.scalar(select(MCPApproval).where(MCPApproval.id == approval_id))


async def get_grant_offer(db: AsyncSession, approval_id: str) -> MCPApprovalGrantOffer | None:
    return await db.get(MCPApprovalGrantOffer, approval_id)


async def list_grant_offers(
    db: AsyncSession,
    approval_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not approval_ids:
        return {}
    offers = list(
        (
            await db.scalars(select(MCPApprovalGrantOffer).where(MCPApprovalGrantOffer.approval_id.in_(approval_ids)))
        ).all()
    )
    return {offer.approval_id: offer.template for offer in offers}


async def list_approvals(
    db: AsyncSession,
    *,
    status: str | None = "pending",
    limit: int = 100,
) -> list[MCPApproval]:
    query = select(MCPApproval).order_by(MCPApproval.requested_at.desc()).limit(limit)
    if status:
        query = query.where(MCPApproval.status == status)
    if status == "pending":
        query = query.where(MCPApproval.expires_at > datetime.now(timezone.utc))
    return list((await db.scalars(query)).all())


async def decide_approval(
    db: AsyncSession,
    *,
    approval_id: str,
    decision: Literal["approved", "denied"],
    actor: str,
    reason: str | None,
    request_id: str,
) -> MCPApproval | None:
    approval = await db.scalar(select(MCPApproval).where(MCPApproval.id == approval_id).with_for_update())
    if approval is None:
        return None
    now = datetime.now(timezone.utc)
    if approval.status != "pending" or _aware(approval.expires_at) <= now:
        raise AuthorizationError("Approval is no longer pending")
    approval.status = decision
    approval.decided_at = now
    approval.decided_by = actor
    approval.decision_reason = reason
    offer = await get_grant_offer(db, approval.id)
    if decision == "approved" and offer is not None:
        from app.mcp.approval_grants import create_grant_from_approval

        await create_grant_from_approval(
            db,
            approval=approval,
            template=offer.template,
            actor=actor,
            request_id=request_id,
        )
    db.add(
        _audit(
            request_id,
            actor,
            f"mcp.approval.{decision}",
            f"{approval.server_name}/{approval.tool_name}",
            {"approval_id": approval.id, "requester_user_id": approval.user_id, "reason": reason},
        )
    )
    await db.commit()
    return approval


def issue_approval_token(approval: MCPApproval, settings: Settings) -> str:
    if approval.status != "approved" or approval.consumed_at is not None:
        raise AuthorizationError("Approval is not executable")
    if _aware(approval.expires_at) <= datetime.now(timezone.utc):
        raise AuthorizationError("Approval has expired")
    payload = {
        "approval_id": approval.id,
        "user_id": approval.user_id,
        "server": approval.server_name,
        "tool": approval.tool_name,
        "arguments_hash": approval.arguments_hash,
        "policy_version": approval.policy_version,
        "exp": int(_aware(approval.expires_at).timestamp()),
    }
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


async def consume_approval(
    db: AsyncSession,
    *,
    token: str,
    settings: Settings,
    user_id: str,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    policy_version: str,
    request_id: str,
) -> MCPApproval:
    payload = _verify_token(token, settings)
    expected = {
        "user_id": user_id,
        "server": server,
        "tool": tool,
        "arguments_hash": arguments_hash(arguments),
        "policy_version": policy_version,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise AuthorizationError("Approval token is not bound to this invocation")
    if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
        raise AuthorizationError("Approval token has expired")

    approval = await db.scalar(
        select(MCPApproval).where(MCPApproval.id == str(payload.get("approval_id", ""))).with_for_update()
    )
    if approval is None or approval.status != "approved" or approval.consumed_at is not None:
        raise AuthorizationError("Approval token is invalid or has already been consumed")
    if _aware(approval.expires_at) <= datetime.now(timezone.utc):
        raise AuthorizationError("Approval has expired")
    approval.status = "consumed"
    approval.consumed_at = datetime.now(timezone.utc)
    db.add(
        _audit(
            request_id,
            user_id,
            "mcp.approval.consumed",
            f"{server}/{tool}",
            {"approval_id": approval.id, "policy_version": policy_version},
        )
    )
    await db.commit()
    return approval


def approval_metadata(approval: MCPApproval) -> dict[str, Any]:
    status = approval.status
    if status in {"pending", "approved"} and _aware(approval.expires_at) <= datetime.now(timezone.utc):
        status = "expired"
    return {
        "id": approval.id,
        "user_id": approval.user_id,
        "team_id": approval.team_id,
        "server": approval.server_name,
        "tool": approval.tool_name,
        "arguments": approval.arguments,
        "purpose": approval.purpose,
        "policy_version": approval.policy_version,
        "status": status,
        "requested_at": approval.requested_at,
        "expires_at": approval.expires_at,
        "decided_at": approval.decided_at,
        "decided_by": approval.decided_by,
        "decision_reason": approval.decision_reason,
        "consumed_at": approval.consumed_at,
    }


def _verify_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ValueError
        payload = json.loads(_unb64(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthorizationError("Invalid approval token") from exc
    if not isinstance(payload, dict):
        raise AuthorizationError("Invalid approval token")
    return payload


def _audit(request_id: str, user_id: str, action: str, resource: str, metadata: dict) -> AuditLog:
    return AuditLog(
        id=str(uuid.uuid4()),
        request_id=request_id,
        user_id=user_id,
        action=action,
        resource=resource,
        metadata_=metadata,
    )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
