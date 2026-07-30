"""MCP tool discovery, authorization, approvals, and invocation brokering."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, resolve_identity
from app.core.exceptions import AuthorizationError, ProxyError
from app.db.engine import get_db
from app.db.models import AuditLog
from app.mcp.approvals import (
    approval_metadata,
    arguments_hash,
    consume_approval,
    create_approval,
    get_approval,
    issue_approval_token,
)
from app.mcp.client import MCPStreamableHTTPClient
from app.mcp.policy import MCPPolicyDecision, MCPPolicyEngine, has_any_mcp_scope
from app.mcp.results import sanitize_tool_result
from app.metrics import prometheus as metrics
from app.pii.scrubber import PIIScrubber, get_scrubber
from app.schemas.mcp import MCPInvokeRequest
from app.telemetry import annotate_current_span

router = APIRouter(tags=["mcp"])


@router.get("/mcp/servers")
async def list_mcp_servers(
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
):
    _require_mcp_access(identity, settings)
    items = []
    for name, server in settings.mcp_servers.items():
        if server.get("enabled", True) is False:
            continue
        items.append(
            {
                "name": name,
                "description": server.get("description", ""),
                "transport": "streamable_http",
            }
        )
    return {"items": items, "policy_version": settings.mcp_active_policy_version}


@router.get("/mcp/servers/{server_name}/tools")
async def list_mcp_tools(
    server_name: str,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
):
    _require_mcp_access(identity, settings)
    client = MCPStreamableHTTPClient(settings)
    tools = await client.list_tools(server_name)
    engine = MCPPolicyEngine(settings)
    visible = []
    for tool in tools:
        name = str(tool.get("name", ""))
        decision = engine.authorize(identity, server_name, name)
        if decision.action == "deny":
            continue
        visible.append(
            {
                **tool,
                "relay": {
                    "authorization": decision.action,
                    "policy_version": decision.policy_version,
                },
            }
        )
    return {"items": visible, "policy_version": settings.mcp_active_policy_version}


@router.post("/mcp/servers/{server_name}/tools/{tool_name}/invoke")
async def invoke_mcp_tool(
    server_name: str,
    tool_name: str,
    body: MCPInvokeRequest,
    request: Request,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
    scrubber: PIIScrubber = Depends(get_scrubber),
    db: AsyncSession = Depends(get_db),
):
    _require_mcp_access(identity, settings)
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    decision = MCPPolicyEngine(settings).authorize(identity, server_name, tool_name, body.arguments)
    _record_decision_metric(server_name, tool_name, decision)
    annotate_current_span(
        **{
            "relay.endpoint": "mcp.tools.call",
            "relay.mcp.server": server_name,
            "relay.mcp.tool": tool_name,
            "relay.mcp.policy_version": decision.policy_version,
            "relay.mcp.authorization": decision.action,
            "relay.user_id": identity.user_id,
            "relay.team_id": identity.team_id,
        }
    )
    if decision.action == "deny":
        await _audit(
            db,
            request_id=request_id,
            user_id=identity.user_id,
            action="mcp.tool.denied",
            resource=f"{server_name}/{tool_name}",
            metadata={
                "reason": decision.reason,
                "policy_version": decision.policy_version,
                "arguments_hash": arguments_hash(body.arguments),
            },
        )
        raise AuthorizationError(decision.reason)

    approval_id: str | None = None
    if decision.action == "require_approval":
        if not body.approval_token:
            approval = await create_approval(
                db,
                user_id=identity.user_id,
                team_id=identity.team_id,
                server=server_name,
                tool=tool_name,
                arguments=body.arguments,
                purpose=body.purpose,
                policy_version=decision.policy_version,
                ttl_seconds=settings.mcp__approval_ttl_seconds,
                request_id=request_id,
            )
            metrics.MCP_APPROVALS.labels(status="requested").inc()
            return JSONResponse(
                status_code=202,
                content={
                    "status": "approval_required",
                    "approval": _json_metadata(approval_metadata(approval)),
                },
            )
        approval = await consume_approval(
            db,
            token=body.approval_token,
            settings=settings,
            user_id=identity.user_id,
            server=server_name,
            tool=tool_name,
            arguments=body.arguments,
            policy_version=decision.policy_version,
            request_id=request_id,
        )
        approval_id = approval.id
        metrics.MCP_APPROVALS.labels(status="consumed").inc()

    started = time.monotonic()
    try:
        result = await MCPStreamableHTTPClient(settings).call_tool(server_name, tool_name, body.arguments)
        sanitized, pii_count = sanitize_tool_result(result, scrubber, max_bytes=settings.mcp__max_result_bytes)
        latency = time.monotonic() - started
        tool_status = "tool_error" if sanitized.get("isError", False) else "success"
        metrics.MCP_TOOL_CALLS.labels(server=server_name, tool=tool_name, status=tool_status).inc()
        metrics.MCP_TOOL_LATENCY.labels(server=server_name, tool=tool_name).observe(latency)
        if pii_count:
            metrics.PII_ENTITIES_SCRUBBED.inc(pii_count)
            metrics.PII_REQUESTS_AFFECTED.inc()
        await _audit(
            db,
            request_id=request_id,
            user_id=identity.user_id,
            action="mcp.tool.called",
            resource=f"{server_name}/{tool_name}",
            metadata={
                "team_id": identity.team_id,
                "arguments_hash": arguments_hash(body.arguments),
                "policy_version": decision.policy_version,
                "approval_id": approval_id,
                "latency_ms": int(latency * 1000),
                "pii_entities_scrubbed": pii_count,
                "is_error": bool(sanitized.get("isError", False)),
            },
        )
        return {
            "server": server_name,
            "tool": tool_name,
            "policy_version": decision.policy_version,
            "approval_id": approval_id,
            "result": sanitized,
        }
    except ProxyError as exc:
        latency = time.monotonic() - started
        metrics.MCP_TOOL_CALLS.labels(server=server_name, tool=tool_name, status="error").inc()
        metrics.MCP_TOOL_LATENCY.labels(server=server_name, tool=tool_name).observe(latency)
        await _audit(
            db,
            request_id=request_id,
            user_id=identity.user_id,
            action="mcp.tool.failed",
            resource=f"{server_name}/{tool_name}",
            metadata={
                "policy_version": decision.policy_version,
                "arguments_hash": arguments_hash(body.arguments),
                "approval_id": approval_id,
                "error_code": exc.error_code,
                "latency_ms": int(latency * 1000),
            },
        )
        raise


@router.get("/mcp/approvals/{approval_id}")
async def approval_status(
    approval_id: str,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_mcp_access(identity, settings)
    approval = await get_approval(db, approval_id)
    if approval is None or approval.user_id != identity.user_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    response = _json_metadata(approval_metadata(approval))
    if approval.status == "approved":
        response["approval_token"] = issue_approval_token(approval, settings)
    return response


def _require_mcp_access(identity: ResolvedIdentity, settings: Settings) -> None:
    if not settings.mcp_enabled:
        raise HTTPException(status_code=404, detail="MCP gateway is disabled")
    if identity.passthrough_key or not has_any_mcp_scope(identity):
        raise AuthorizationError("An MCP-scoped Relay API key is required")


def _record_decision_metric(server: str, tool: str, decision: MCPPolicyDecision) -> None:
    metrics.MCP_POLICY_DECISIONS.labels(
        server=server,
        tool=tool,
        action=decision.action,
        policy_version=decision.policy_version,
    ).inc()


async def _audit(
    db: AsyncSession,
    *,
    request_id: str,
    user_id: str,
    action: str,
    resource: str,
    metadata: dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            request_id=request_id,
            user_id=user_id,
            action=action,
            resource=resource,
            metadata_=metadata,
        )
    )
    await db.commit()


def _json_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in value.items()}
