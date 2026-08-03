"""Bridge Relay's MCP policy/approvals into native Responses API MCP tools."""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mcp_gateway import gateway_tool_name, parse_gateway_tool_name
from app.api.v1.mcp import list_mcp_tools
from app.config import Settings
from app.core.auth import ResolvedIdentity
from app.core.exceptions import ApprovalRequiredError, AuthorizationError, ContentPolicyError
from app.db.models import MCPResponseApproval
from app.mcp.approval_grants import consume_matching_grant
from app.mcp.approvals import create_approval, decide_approval, get_approval
from app.mcp.grants import issue_mcp_grant
from app.mcp.policy import has_tool_scope
from app.mcp.policy_store import active_policy_engine
from app.metrics import prometheus as metrics
from app.schemas.responses import ResponsesRequest

_SERVER_LABEL = "relay"


async def prepare_responses_mcp_tools(
    request: ResponsesRequest,
    identity: ResolvedIdentity,
    settings: Settings,
    db: AsyncSession,
) -> list[dict[str, Any]] | None:
    """Return upstream tools with a delegated Relay MCP tool when requested."""
    approval_responses = _approval_responses(request.input)
    if not request.relay_mcp_servers and not approval_responses:
        return request.tools
    _validate_mode(request, settings)
    _ensure_no_reserved_server_label(request.tools)

    if approval_responses:
        if len(approval_responses) != 1:
            raise ContentPolicyError("Relay currently accepts one MCP approval response per continuation")
        relay_tool = await _resume_tool(
            approval_responses[0],
            request.previous_response_id,
            identity,
            settings,
            db,
        )
    else:
        relay_tool = await _initial_tool(request.relay_mcp_servers or [], identity, settings, db)
    return [*(request.tools or []), relay_tool]


async def persist_responses_mcp_approvals(
    payload: dict[str, Any],
    request: ResponsesRequest,
    identity: ResolvedIdentity,
    settings: Settings,
    db: AsyncSession,
    request_id: str,
) -> None:
    """Create Relay approval records for native upstream MCP approval items."""
    if not request.relay_mcp_servers:
        return
    response_id = str(payload.get("id", ""))
    for item in payload.get("output", []):
        if item.get("type") != "mcp_approval_request" or item.get("server_label") != _SERVER_LABEL:
            continue
        provider_approval_id = str(item.get("id", ""))
        if not provider_approval_id or await _mapping(db, provider_approval_id):
            continue
        try:
            server, tool = parse_gateway_tool_name(str(item.get("name", "")))
            arguments = json.loads(item.get("arguments") or "{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthorizationError("Upstream returned an invalid Relay MCP approval request") from exc
        if server not in request.relay_mcp_servers or not isinstance(arguments, dict):
            raise AuthorizationError("Upstream MCP approval is outside the requested Relay server set")
        engine, _snapshot = await active_policy_engine(db, settings)
        decision = engine.authorize(identity, server, tool, arguments)
        if decision.action == "deny":
            raise AuthorizationError(f"Upstream MCP approval violates Relay policy: {decision.reason}")
        standing_grant = None
        if decision.action == "require_approval":
            standing_grant = await consume_matching_grant(
                db,
                identity=identity,
                server=server,
                tool=tool,
                arguments=arguments,
                policy_version=decision.policy_version,
                request_id=request_id,
            )
        approval = await create_approval(
            db,
            user_id=identity.user_id,
            team_id=identity.team_id,
            server=server,
            tool=tool,
            arguments=arguments,
            purpose=request.relay_mcp_purpose,
            policy_version=decision.policy_version,
            ttl_seconds=settings.mcp__approval_ttl_seconds,
            request_id=request_id,
            grant_template=decision.grant if standing_grant is None else None,
        )
        metrics.MCP_APPROVALS.labels(status="requested").inc()
        if standing_grant is not None or decision.action == "allow":
            approved_by = "approval-grant" if standing_grant else "policy"
            approval = await decide_approval(
                db,
                approval_id=approval.id,
                decision="approved",
                actor=approved_by,
                reason="Authorized without a manual approval pause",
                request_id=request_id,
            )
            assert approval is not None
            metrics.MCP_APPROVALS.labels(status="approved").inc()
        db.add(
            MCPResponseApproval(
                id=str(uuid.uuid4()),
                provider_response_id=response_id,
                provider_approval_request_id=provider_approval_id,
                approval_id=approval.id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
        )
        await db.commit()
        item["relay_approval"] = {
            "id": approval.id,
            "status": approval.status,
            "grant_id": standing_grant.id if standing_grant else None,
        }


async def _initial_tool(
    servers: list[str],
    identity: ResolvedIdentity,
    settings: Settings,
    db: AsyncSession,
) -> dict[str, Any]:
    if not servers:
        raise ContentPolicyError("relay_mcp_servers must contain at least one configured server")
    unknown = sorted(set(servers) - set(settings.mcp_servers))
    if unknown:
        raise ContentPolicyError(f"Unknown Relay MCP server(s): {', '.join(unknown)}")
    allowed: list[str] = []
    automatic: list[str] = []
    for server in dict.fromkeys(servers):
        response = await list_mcp_tools(server, identity, settings, db)
        for tool in response["items"]:
            name = gateway_tool_name(server, str(tool["name"]))
            allowed.append(name)
            if tool["relay"]["authorization"] == "allow":
                automatic.append(name)
    if not allowed:
        raise AuthorizationError("No MCP tools are authorized for this identity")
    grant = issue_mcp_grant(
        settings,
        user_id=identity.user_id,
        team_id=identity.team_id,
        scopes=_delegated_scopes(identity.scopes, servers),
    )
    require_approval: str | dict[str, Any]
    if len(automatic) == len(allowed):
        require_approval = "never"
    elif automatic:
        require_approval = {"never": {"tool_names": automatic}}
    else:
        require_approval = "always"
    return _native_tool(settings, grant, allowed, require_approval)


async def _resume_tool(
    response: dict[str, Any],
    previous_response_id: str | None,
    identity: ResolvedIdentity,
    settings: Settings,
    db: AsyncSession,
) -> dict[str, Any]:
    provider_id = str(response.get("approval_request_id", ""))
    mapping = await _mapping(db, provider_id)
    if mapping is None or mapping.user_id != identity.user_id or mapping.provider_response_id != previous_response_id:
        raise AuthorizationError("MCP approval response does not belong to this identity")
    approval = await get_approval(db, mapping.approval_id)
    if approval is None:
        raise AuthorizationError("Relay MCP approval no longer exists")
    if not has_tool_scope(identity, approval.server_name, approval.tool_name):
        raise AuthorizationError("The caller is no longer scoped for this MCP tool")
    if not isinstance(response.get("approve"), bool):
        raise ContentPolicyError("MCP approval response must include a boolean approve field")
    approve = response["approve"]
    if not approve:
        if approval.status == "pending":
            await decide_approval(
                db,
                approval_id=approval.id,
                decision="denied",
                actor=identity.user_id,
                reason="Requester rejected the Responses API MCP call",
                request_id=str(uuid.uuid4()),
            )
        grant = issue_mcp_grant(
            settings,
            user_id=identity.user_id,
            team_id=identity.team_id,
            scopes=[f"mcp:{approval.server_name}:{approval.tool_name}"],
        )
        return _native_tool(settings, grant, [gateway_tool_name(approval.server_name, approval.tool_name)], "always")
    if approval.status == "pending":
        raise ApprovalRequiredError(f"Relay approval {approval.id} is still pending")
    if approval.status != "approved":
        raise AuthorizationError(f"Relay approval {approval.id} is {approval.status}")
    grant = issue_mcp_grant(
        settings,
        user_id=identity.user_id,
        team_id=identity.team_id,
        scopes=[f"mcp:{approval.server_name}:{approval.tool_name}"],
        approval_id=approval.id,
        server=approval.server_name,
        tool=approval.tool_name,
        arguments_hash=approval.arguments_hash,
    )
    return _native_tool(
        settings,
        grant,
        [gateway_tool_name(approval.server_name, approval.tool_name)],
        "never",
    )


def _native_tool(
    settings: Settings,
    grant: str,
    allowed_tools: list[str],
    require_approval: str | dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "mcp",
        "server_label": _SERVER_LABEL,
        "server_description": "Enterprise tools authorized and audited by Relay",
        "server_url": settings.mcp__public_url.rstrip("/"),
        "authorization": grant,
        "allowed_tools": allowed_tools,
        "require_approval": require_approval,
    }


def _validate_mode(request: ResponsesRequest, settings: Settings) -> None:
    if not settings.mcp_enabled:
        raise ContentPolicyError("Relay MCP is disabled")
    parsed = urlparse(settings.mcp__public_url)
    if parsed.scheme != "https" and not settings.mcp__allow_insecure_http:
        raise ContentPolicyError("mcp.public_url must be an HTTPS URL")
    if not parsed.netloc:
        raise ContentPolicyError("mcp.public_url must point to Relay's public /mcp endpoint")
    if request.stream:
        raise ContentPolicyError("Relay-managed MCP approvals currently require stream=false")
    if request.store is not True:
        raise ContentPolicyError("Relay-managed MCP continuations currently require store=true")
    if request.background:
        raise ContentPolicyError("Relay-managed MCP does not support background mode")


def _approval_responses(input_: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(input_, str):
        return []
    return [item for item in input_ if item.get("type") == "mcp_approval_response"]


def _ensure_no_reserved_server_label(tools: list[dict[str, Any]] | None) -> None:
    if any(tool.get("type") == "mcp" and tool.get("server_label") == _SERVER_LABEL for tool in tools or []):
        raise ContentPolicyError("The MCP server_label 'relay' is reserved by Relay")


async def _mapping(db: AsyncSession, provider_approval_id: str) -> MCPResponseApproval | None:
    return await db.scalar(
        select(MCPResponseApproval).where(MCPResponseApproval.provider_approval_request_id == provider_approval_id)
    )


def _delegated_scopes(scopes: list[str], servers: list[str]) -> list[str]:
    selected = set(servers)
    delegated = []
    for scope in scopes:
        if scope in {"*", "mcp", "mcp:*"}:
            delegated.extend(f"mcp:{server}:*" for server in selected)
        elif scope.startswith("mcp:") and scope.split(":", 2)[1] in selected:
            delegated.append(scope)
    return sorted(set(delegated))
