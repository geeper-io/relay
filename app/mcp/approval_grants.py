"""Durable standing grants for repeated, policy-constrained MCP calls."""

from __future__ import annotations

import fnmatch
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ResolvedIdentity
from app.core.exceptions import AuthorizationError
from app.db.models import AuditLog, MCPApproval, MCPApprovalGrant
from app.mcp.policy import validate_argument_constraints

GrantSubject = Literal["user", "team"]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def grant_status(grant: MCPApprovalGrant, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if grant.revoked_at is not None:
        return "revoked"
    if _aware(grant.expires_at) <= current:
        return "expired"
    if grant.calls_used >= grant.max_calls:
        return "exhausted"
    return "active"


def grant_metadata(grant: MCPApprovalGrant, *, active_policy_version: str | None = None) -> dict[str, Any]:
    status = grant_status(grant)
    policy_active = active_policy_version is None or grant.policy_version == active_policy_version
    if status == "active" and not policy_active:
        status = "policy_stale"
    return {
        "id": grant.id,
        "subject_type": grant.subject_type,
        "subject_id": grant.subject_id,
        "server": grant.server_name,
        "tool": grant.tool_pattern,
        "constraints": grant.constraints or {},
        "policy_version": grant.policy_version,
        "max_calls": grant.max_calls,
        "calls_used": grant.calls_used,
        "calls_remaining": max(0, grant.max_calls - grant.calls_used),
        "status": status,
        "policy_active": policy_active,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
        "created_by": grant.created_by,
        "reason": grant.reason,
        "source_approval_id": grant.source_approval_id,
        "workflow_id": grant.workflow_id,
        "created_at": grant.created_at,
        "updated_at": grant.updated_at,
    }


async def create_approval_grant(
    db: AsyncSession,
    *,
    subject_type: GrantSubject,
    subject_id: str,
    server: str,
    tool_pattern: str,
    constraints: dict[str, Any],
    policy_version: str,
    max_calls: int,
    expires_at: datetime,
    actor: str,
    reason: str | None,
    request_id: str,
    source_approval_id: str | None = None,
    workflow_id: str | None = None,
    commit: bool = True,
) -> MCPApprovalGrant:
    if subject_type not in {"user", "team"}:
        raise AuthorizationError("Approval grant subject must be a user or team")
    if not subject_id or not server or not tool_pattern:
        raise AuthorizationError("Approval grant subject, server, and tool are required")
    if not 1 <= max_calls <= 10_000:
        raise AuthorizationError("Approval grant max_calls must be between 1 and 10000")
    if _aware(expires_at) <= datetime.now(timezone.utc):
        raise AuthorizationError("Approval grant expiry must be in the future")

    grant = MCPApprovalGrant(
        id=str(uuid.uuid4()),
        subject_type=subject_type,
        subject_id=subject_id,
        server_name=server,
        tool_pattern=tool_pattern,
        constraints=constraints,
        policy_version=policy_version,
        max_calls=max_calls,
        expires_at=expires_at,
        created_by=actor,
        reason=reason,
        source_approval_id=source_approval_id,
        workflow_id=workflow_id,
    )
    db.add(grant)
    db.add(
        _audit(
            request_id=request_id,
            actor=actor,
            action="mcp.grant.created",
            grant=grant,
            metadata={
                "subject_type": subject_type,
                "subject_id": subject_id,
                "server": server,
                "tool": tool_pattern,
                "max_calls": max_calls,
                "expires_at": expires_at.isoformat(),
                "source_approval_id": source_approval_id,
                "workflow_id": workflow_id,
            },
        )
    )
    if commit:
        await db.commit()
        await db.refresh(grant)
    return grant


async def create_grant_from_approval(
    db: AsyncSession,
    *,
    approval: MCPApproval,
    template: dict[str, Any],
    actor: str,
    request_id: str,
) -> MCPApprovalGrant:
    ttl_seconds = min(max(int(template.get("ttl_seconds", 3600)), 60), 2_592_000)
    max_calls = min(max(int(template.get("max_calls", 1)), 1), 10_000)
    subject_type: GrantSubject = "team" if template.get("subject") == "team" else "user"
    if subject_type == "team" and not approval.team_id:
        subject_type = "user"
    subject_id = approval.team_id if subject_type == "team" else approval.user_id
    assert subject_id is not None
    return await create_approval_grant(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        server=approval.server_name,
        tool_pattern=str(template.get("tool_pattern") or approval.tool_name),
        constraints=dict(template.get("constraints") or {}),
        policy_version=approval.policy_version,
        max_calls=max_calls,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        actor=actor,
        reason=str(template.get("reason") or approval.decision_reason or "Approved standing access"),
        request_id=request_id,
        source_approval_id=approval.id,
        workflow_id=str(template["workflow_id"]) if template.get("workflow_id") else None,
        commit=False,
    )


async def find_matching_grant(
    db: AsyncSession,
    *,
    identity: ResolvedIdentity,
    server: str,
    tool: str,
    arguments: dict[str, Any] | None,
    policy_version: str,
    require_unconstrained: bool = False,
    lock: bool = False,
) -> MCPApprovalGrant | None:
    now = datetime.now(timezone.utc)
    subjects = [and_(MCPApprovalGrant.subject_type == "user", MCPApprovalGrant.subject_id == identity.user_id)]
    if identity.team_id:
        subjects.append(and_(MCPApprovalGrant.subject_type == "team", MCPApprovalGrant.subject_id == identity.team_id))
    query = (
        select(MCPApprovalGrant)
        .where(
            or_(*subjects),
            MCPApprovalGrant.server_name == server,
            MCPApprovalGrant.policy_version == policy_version,
            MCPApprovalGrant.revoked_at.is_(None),
            MCPApprovalGrant.expires_at > now,
            MCPApprovalGrant.calls_used < MCPApprovalGrant.max_calls,
        )
        .order_by(
            (MCPApprovalGrant.subject_type == "user").desc(),
            MCPApprovalGrant.created_at.desc(),
        )
        .limit(200)
    )
    if lock:
        query = query.with_for_update()
    candidates = list((await db.scalars(query)).all())
    for grant in candidates:
        if not fnmatch.fnmatchcase(tool, grant.tool_pattern):
            continue
        constraints = grant.constraints or {}
        if require_unconstrained and constraints:
            continue
        if arguments is not None:
            try:
                validate_argument_constraints(arguments, constraints)
            except AuthorizationError:
                continue
        return grant
    return None


async def consume_matching_grant(
    db: AsyncSession,
    *,
    identity: ResolvedIdentity,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    policy_version: str,
    request_id: str,
) -> MCPApprovalGrant | None:
    grant = await find_matching_grant(
        db,
        identity=identity,
        server=server,
        tool=tool,
        arguments=arguments,
        policy_version=policy_version,
        lock=True,
    )
    if grant is None:
        return None
    if grant_status(grant) != "active":
        return None
    grant.calls_used += 1
    db.add(
        _audit(
            request_id=request_id,
            actor=identity.user_id,
            action="mcp.grant.consumed",
            grant=grant,
            metadata={
                "server": server,
                "tool": tool,
                "calls_used": grant.calls_used,
                "max_calls": grant.max_calls,
            },
        )
    )
    await db.commit()
    await db.refresh(grant)
    return grant


async def list_approval_grants(
    db: AsyncSession,
    *,
    include_inactive: bool = True,
    limit: int = 200,
) -> list[MCPApprovalGrant]:
    query = select(MCPApprovalGrant).order_by(MCPApprovalGrant.created_at.desc()).limit(limit)
    if not include_inactive:
        now = datetime.now(timezone.utc)
        query = query.where(
            MCPApprovalGrant.revoked_at.is_(None),
            MCPApprovalGrant.expires_at > now,
            MCPApprovalGrant.calls_used < MCPApprovalGrant.max_calls,
        )
    return list((await db.scalars(query)).all())


async def revoke_approval_grant(
    db: AsyncSession,
    *,
    grant_id: str,
    actor: str,
    request_id: str,
) -> MCPApprovalGrant | None:
    grant = await db.scalar(select(MCPApprovalGrant).where(MCPApprovalGrant.id == grant_id).with_for_update())
    if grant is None:
        return None
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        db.add(
            _audit(
                request_id=request_id,
                actor=actor,
                action="mcp.grant.revoked",
                grant=grant,
                metadata={"calls_used": grant.calls_used, "max_calls": grant.max_calls},
            )
        )
        await db.commit()
        await db.refresh(grant)
    return grant


def _audit(
    *,
    request_id: str,
    actor: str,
    action: str,
    grant: MCPApprovalGrant,
    metadata: dict[str, Any],
) -> AuditLog:
    return AuditLog(
        id=str(uuid.uuid4()),
        request_id=request_id,
        user_id=actor,
        action=action,
        resource=f"mcp-grant/{grant.id}",
        metadata_={"grant_id": grant.id, **metadata},
    )
