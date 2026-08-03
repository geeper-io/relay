"""Opt-in, same-origin admin dashboard and approval API."""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.operations import get_admin_overview, get_admin_user_detail, list_admin_users
from app.admin.pages import dashboard_page, login_page
from app.admin.roles import (
    AdminRole,
    list_admin_identities,
    list_admin_roles,
    remove_admin_role,
    resolve_admin_role,
    set_admin_role,
)
from app.admin.session import (
    COOKIE_NAME,
    AdminSession,
    issue_admin_session,
    set_admin_session_cookie,
    verify_admin_session,
)
from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity
from app.core.exceptions import AuthenticationError, ProxyError
from app.db.engine import get_db
from app.db.models import AdminRoleAssignment, Team, User
from app.mcp.approval_grants import (
    create_approval_grant,
    find_matching_grant,
    grant_metadata,
    list_approval_grants,
    revoke_approval_grant,
)
from app.mcp.approvals import approval_metadata, decide_approval, list_approvals, list_grant_offers
from app.mcp.client import MCPStreamableHTTPClient
from app.mcp.policy import MCPPolicyEngine
from app.mcp.policy_store import (
    activate_policy_version,
    create_policy_draft,
    list_policy_activations,
    list_policy_versions,
    load_active_policy,
    load_policy_version,
    policy_diff,
    validate_policy_document,
)
from app.metrics import prometheus as metrics
from app.schemas.mcp import (
    MCPApprovalDecisionRequest,
    MCPApprovalGrantCreateRequest,
    MCPPolicyActivationRequest,
    MCPPolicyDocumentRequest,
    MCPPolicyDraftCreateRequest,
    MCPPolicySimulationRequest,
)

router = APIRouter(tags=["admin-dashboard"])


class DashboardAdminRoleRequest(BaseModel):
    role: AdminRole


def _require_enabled(settings: Settings) -> None:
    if not settings.admin__enabled:
        raise HTTPException(status_code=404, detail="Admin dashboard is disabled")


def _session_from_request(request: Request, settings: Settings) -> AdminSession:
    _require_enabled(settings)
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise AuthenticationError("Admin sign-in required")
    return verify_admin_session(token, settings)


async def _validated_session(
    request: Request,
    settings: Settings,
    db: AsyncSession,
) -> AdminSession:
    session = _session_from_request(request, settings)
    if session.user_id:
        current_role = await resolve_admin_role(
            db,
            user_id=session.user_id,
            email=session.email or "",
            settings=settings,
        )
        if current_role != session.role:
            raise AuthenticationError("Admin role changed; sign in again")
    return session


async def require_dashboard_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> AdminSession:
    return await _validated_session(request, settings, db)


def _html_response(content: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        content,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'self' https://cdn.jsdelivr.net; "
                "script-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.get("/admin/login", include_in_schema=False)
async def admin_login_page(settings: Settings = Depends(get_settings)):
    _require_enabled(settings)
    return _html_response(
        login_page(
            oidc_enabled=settings.admin__oidc_enabled and settings.oauth_enabled,
            master_key_enabled=settings.admin__allow_master_key_login,
        )
    )


@router.post("/admin/login", include_in_schema=False)
async def admin_login(
    master_key: Annotated[str, Form()],
    settings: Settings = Depends(get_settings),
):
    _require_enabled(settings)
    if not settings.admin__allow_master_key_login:
        raise HTTPException(status_code=404, detail="Master-key dashboard login is disabled")
    if not hmac.compare_digest(master_key, settings.proxy_master_key):
        return _html_response(
            login_page(
                error="The admin key was not accepted.",
                oidc_enabled=settings.admin__oidc_enabled and settings.oauth_enabled,
                master_key_enabled=True,
            ),
            status_code=401,
        )
    token, _session = issue_admin_session(settings, role="admin", actor="master-key")
    response = RedirectResponse("/admin", status_code=303)
    set_admin_session_cookie(response, token, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin", include_in_schema=False)
async def admin_dashboard(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled(settings)
    try:
        session = await _validated_session(request, settings, db)
    except AuthenticationError:
        return RedirectResponse("/admin/login", status_code=303)
    return _html_response(
        dashboard_page(
            csrf_token=session.csrf_token,
            session_expires_at=session.expires_at,
            role=session.role,
            display_name=session.display_name,
            email=session.email,
        )
    )


@router.post("/admin/logout", include_in_schema=False)
async def admin_logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
    settings: Settings = Depends(get_settings),
):
    session = _session_from_request(request, settings)
    if not hmac.compare_digest(csrf_token, session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/admin")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin/api/overview")
async def dashboard_overview(
    days: int = Query(default=7, ge=1, le=365),
    _session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    return await get_admin_overview(db, days=days)


@router.get("/admin/api/users")
async def dashboard_users(
    q: str | None = Query(default=None, max_length=255),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    return await list_admin_users(
        db,
        settings,
        query=q,
        days=days,
        limit=limit,
        offset=offset,
    )


@router.get("/admin/api/users/{user_id}")
async def dashboard_user_detail(
    user_id: str,
    days: int = Query(default=30, ge=1, le=365),
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_admin_user_detail(db, settings, user_id=user_id, days=days)
    if detail is None:
        raise HTTPException(status_code=404, detail="User not found")
    return detail


@router.get("/admin/api/mcp/approvals")
async def dashboard_approval_queue(
    status: str | None = Query(default="pending"),
    limit: int = Query(default=200, ge=1, le=500),
    _session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    normalized_status = status or None
    if normalized_status not in {None, "pending", "approved", "denied", "consumed"}:
        raise HTTPException(status_code=400, detail="Invalid approval status")
    approvals = await list_approvals(db, status=normalized_status, limit=limit)
    offers = await list_grant_offers(db, [item.id for item in approvals])
    return {
        "items": [_json_metadata({**approval_metadata(item), "grant_offer": offers.get(item.id)}) for item in approvals]
    }


@router.post("/admin/api/mcp/approvals/{approval_id}/decision")
async def dashboard_approval_decision(
    approval_id: str,
    body: MCPApprovalDecisionRequest,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if session.role not in {"approver", "admin"}:
        raise HTTPException(status_code=403, detail="Approver role required")
    approval = await decide_approval(
        db,
        approval_id=approval_id,
        decision=body.decision,
        actor=session.user_id or session.actor,
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    metrics.MCP_APPROVALS.labels(status=body.decision).inc()
    return _json_metadata(approval_metadata(approval))


@router.get("/admin/api/mcp/grants")
async def dashboard_grant_inventory(
    include_inactive: bool = True,
    limit: int = Query(default=200, ge=1, le=500),
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    grants = await list_approval_grants(db, include_inactive=include_inactive, limit=limit)
    active_policy = await load_active_policy(db, settings)
    return {
        "items": [_json_metadata(grant_metadata(item, active_policy_version=active_policy.version)) for item in grants]
    }


@router.post("/admin/api/mcp/grants", status_code=201)
async def dashboard_create_grant(
    body: MCPApprovalGrantCreateRequest,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    if body.server not in settings.mcp_servers:
        raise HTTPException(status_code=400, detail="MCP server is not registered")
    subject_model = User if body.subject_type == "user" else Team
    if await db.get(subject_model, body.subject_id) is None:
        raise HTTPException(status_code=404, detail=f"Grant {body.subject_type} not found")
    active_policy = await load_active_policy(db, settings)
    grant = await create_approval_grant(
        db,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        server=body.server,
        tool_pattern=body.tool,
        constraints=body.constraints,
        policy_version=active_policy.version,
        max_calls=body.max_calls,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds),
        actor=session.user_id or session.actor,
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
        workflow_id=body.workflow_id,
    )
    return _json_metadata(grant_metadata(grant, active_policy_version=active_policy.version))


@router.delete("/admin/api/mcp/grants/{grant_id}")
async def dashboard_revoke_grant(
    grant_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    grant = await revoke_approval_grant(
        db,
        grant_id=grant_id,
        actor=session.user_id or session.actor,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Approval grant not found")
    active_policy = await load_active_policy(db, settings)
    return _json_metadata(grant_metadata(grant, active_policy_version=active_policy.version))


@router.get("/admin/api/mcp/servers")
async def dashboard_mcp_servers(
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
):
    client = MCPStreamableHTTPClient(settings)

    async def probe(name: str, server: dict) -> dict:
        if server.get("enabled", True) is False:
            return {
                "name": name,
                "description": server.get("description", ""),
                "transport": server.get("transport", "streamable_http"),
                "status": "disabled",
                "latency_ms": None,
                "tool_count": 0,
                "tools": [],
            }
        started = time.monotonic()
        try:
            tools = await asyncio.wait_for(
                client.list_tools(name),
                timeout=min(5.0, settings.mcp__request_timeout_seconds),
            )
            return {
                "name": name,
                "description": server.get("description", ""),
                "transport": server.get("transport", "streamable_http"),
                "status": "healthy",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "tool_count": len(tools),
                "tools": [
                    {
                        "name": str(tool.get("name", "")),
                        "title": str(tool.get("title", "")),
                        "description": str(tool.get("description", "")),
                        "input_schema": tool.get("inputSchema", {}),
                    }
                    for tool in tools
                ],
            }
        except (ProxyError, TimeoutError) as exc:
            return {
                "name": name,
                "description": server.get("description", ""),
                "transport": server.get("transport", "streamable_http"),
                "status": "unhealthy",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "tool_count": 0,
                "tools": [],
                "error": exc.message if isinstance(exc, ProxyError) else "Health check timed out",
            }

    items = await asyncio.gather(*(probe(name, server) for name, server in settings.mcp_servers.items()))
    return {"items": items}


@router.get("/admin/api/mcp/policies")
async def dashboard_mcp_policies(
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    active = await load_active_policy(db, settings)
    stored = await list_policy_versions(db)
    stored_versions = {item.version for item in stored}
    items = [
        {
            "version": item.version,
            "status": item.status,
            "source": "database",
            "base_version": item.base_version,
            "document": item.document,
            "created_by": item.created_by,
            "reason": item.reason,
            "created_at": item.created_at,
            "activated_by": item.activated_by,
            "activated_at": item.activated_at,
            "diff_from_active": policy_diff(active.document, item.document),
        }
        for item in stored
    ]
    for version, document in settings.mcp_policies.items():
        if version in stored_versions:
            continue
        items.append(
            {
                "version": version,
                "status": "active" if active.source == "configuration" and active.version == version else "configured",
                "source": "configuration",
                "base_version": None,
                "document": document,
                "created_by": "configuration",
                "reason": None,
                "created_at": None,
                "activated_by": None,
                "activated_at": None,
                "diff_from_active": policy_diff(active.document, document),
            }
        )
    activations = await list_policy_activations(db)
    return {
        "active": {"version": active.version, "source": active.source, "document": active.document},
        "items": [_json_metadata(item) for item in items],
        "activations": [
            _json_metadata(
                {
                    "id": item.id,
                    "version": item.version,
                    "previous_version": item.previous_version,
                    "actor": item.actor,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
            )
            for item in activations
        ],
    }


@router.post("/admin/api/mcp/policies/validate")
async def dashboard_validate_mcp_policy(
    body: MCPPolicyDocumentRequest,
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    validation = validate_policy_document(body.document, settings)
    active = await load_active_policy(db, settings)
    return {
        "valid": validation.valid,
        "errors": validation.errors,
        "warnings": validation.warnings,
        "summary": {"rule_count": validation.rule_count, "actions": validation.actions},
        "diff_from_active": policy_diff(active.document, body.document),
    }


@router.post("/admin/api/mcp/policies/simulate")
async def dashboard_simulate_mcp_policy(
    body: MCPPolicySimulationRequest,
    _session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    if body.document is not None and body.version is not None:
        raise HTTPException(status_code=400, detail="Choose either a policy version or an inline document")
    if body.document is not None:
        validation = validate_policy_document(body.document, settings)
        if not validation.valid:
            raise HTTPException(status_code=400, detail="Invalid policy: " + "; ".join(validation.errors))
        active = await load_active_policy(db, settings)
        snapshot_version = f"simulation:{active.version}"
        snapshot_document = body.document
        source = "inline"
    elif body.version is not None:
        snapshot = await load_policy_version(db, settings, body.version)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Policy version not found")
        snapshot_version = snapshot.version
        snapshot_document = snapshot.document
        source = snapshot.source
    else:
        snapshot = await load_active_policy(db, settings)
        snapshot_version = snapshot.version
        snapshot_document = snapshot.document
        source = snapshot.source
    identity = ResolvedIdentity(
        user_id=body.user_id,
        team_id=body.team_id,
        key_id=None,
        scopes=body.scopes,
    )
    decision = MCPPolicyEngine(
        settings,
        policy_version=snapshot_version,
        policy_document=snapshot_document,
    ).authorize(identity, body.server, body.tool, body.arguments)
    grant = await find_matching_grant(
        db,
        identity=identity,
        server=body.server,
        tool=body.tool,
        arguments=body.arguments,
        policy_version=decision.policy_version,
    )
    return {
        "action": decision.action,
        "reason": decision.reason,
        "rule_name": decision.rule_name,
        "policy_version": decision.policy_version,
        "policy_source": source,
        "constraints": decision.constraints or {},
        "grant_offer": decision.grant,
        "standing_grant": grant_metadata(grant, active_policy_version=decision.policy_version) if grant else None,
        "effective_action": "allow" if decision.action == "require_approval" and grant else decision.action,
    }


@router.post("/admin/api/mcp/policies/drafts", status_code=201)
async def dashboard_create_mcp_policy_draft(
    body: MCPPolicyDraftCreateRequest,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    policy = await create_policy_draft(
        db,
        version=body.version,
        document=body.document,
        base_version=body.base_version,
        actor=session.user_id or session.actor,
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
        settings=settings,
    )
    return _json_metadata(
        {
            "version": policy.version,
            "status": policy.status,
            "base_version": policy.base_version,
            "document": policy.document,
            "created_by": policy.created_by,
            "reason": policy.reason,
            "created_at": policy.created_at,
        }
    )


@router.post("/admin/api/mcp/policies/{version}/activate")
async def dashboard_activate_mcp_policy(
    version: str,
    body: MCPPolicyActivationRequest,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    policy = await activate_policy_version(
        db,
        version=version,
        actor=session.user_id or session.actor,
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
        settings=settings,
    )
    return _json_metadata(
        {
            "version": policy.version,
            "status": policy.status,
            "activated_by": policy.activated_by,
            "activated_at": policy.activated_at,
        }
    )


@router.get("/admin/api/admin-roles")
async def dashboard_admin_roles(
    role: AdminRole | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    _require_role_admin(session)
    assignments = await list_admin_roles(db, role=role, limit=limit)
    return {"items": [_admin_role_metadata(item) for item in assignments]}


@router.get("/admin/api/admin-identities")
async def dashboard_admin_identities(
    limit: int = Query(default=200, ge=1, le=500),
    session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    _require_role_admin(session)
    identities = await list_admin_identities(db, limit=limit)
    return {
        "items": [
            {
                "user_id": identity.user_id,
                "email": identity.email,
                "display_name": identity.display_name,
                "last_seen_at": identity.last_seen_at,
                "role": assignment.role if assignment else None,
            }
            for identity, assignment in identities
        ]
    }


@router.put("/admin/api/admin-roles/{user_id}")
async def dashboard_set_admin_role(
    user_id: str,
    body: DashboardAdminRoleRequest,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    assignment = await set_admin_role(
        db,
        user_id=user_id,
        role=body.role,
        actor=session.user_id or session.actor,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    return _admin_role_metadata(assignment)


@router.delete("/admin/api/admin-roles/{user_id}", status_code=204)
async def dashboard_remove_admin_role(
    user_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
    session: AdminSession = Depends(require_dashboard_session),
    db: AsyncSession = Depends(get_db),
):
    _require_csrf(session, x_csrf_token)
    _require_role_admin(session)
    removed = await remove_admin_role(
        db,
        user_id=user_id,
        actor=session.user_id or session.actor,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Admin role assignment not found")


def _require_csrf(session: AdminSession, csrf_token: str | None) -> None:
    if not csrf_token or not hmac.compare_digest(csrf_token, session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _require_role_admin(session: AdminSession) -> None:
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def _admin_role_metadata(assignment: AdminRoleAssignment) -> dict:
    return {
        "user_id": assignment.user_id,
        "role": assignment.role,
        "assigned_by": assignment.assigned_by,
        "created_at": assignment.created_at,
        "updated_at": assignment.updated_at,
    }


def _json_metadata(value: dict) -> dict:
    return {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in value.items()}
