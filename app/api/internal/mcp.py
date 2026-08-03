"""Administrative MCP approval queue endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, require_admin
from app.db.engine import get_db
from app.db.models import Team, User
from app.mcp.approval_grants import (
    create_approval_grant,
    find_matching_grant,
    grant_metadata,
    list_approval_grants,
    revoke_approval_grant,
)
from app.mcp.approvals import approval_metadata, decide_approval, list_approvals, list_grant_offers
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

router = APIRouter(tags=["mcp-admin"], dependencies=[Depends(require_admin)])


@router.get("/mcp/approvals")
async def approval_queue(
    status: str | None = "pending",
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    approvals = await list_approvals(db, status=status, limit=limit)
    offers = await list_grant_offers(db, [item.id for item in approvals])
    return {
        "items": [_json_metadata({**approval_metadata(item), "grant_offer": offers.get(item.id)}) for item in approvals]
    }


@router.post("/mcp/approvals/{approval_id}/decision")
async def approval_decision(
    approval_id: str,
    body: MCPApprovalDecisionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    approval = await decide_approval(
        db,
        approval_id=approval_id,
        decision=body.decision,
        actor="admin",
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    metrics.MCP_APPROVALS.labels(status=body.decision).inc()
    return _json_metadata(approval_metadata(approval))


@router.get("/mcp/grants")
async def grant_inventory(
    include_inactive: bool = True,
    limit: int = Query(default=200, ge=1, le=500),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    grants = await list_approval_grants(db, include_inactive=include_inactive, limit=limit)
    active_policy = await load_active_policy(db, settings)
    return {
        "items": [_json_metadata(grant_metadata(item, active_policy_version=active_policy.version)) for item in grants]
    }


@router.post("/mcp/grants", status_code=201)
async def create_grant_endpoint(
    body: MCPApprovalGrantCreateRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
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
        actor="master-key",
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
        workflow_id=body.workflow_id,
    )
    return _json_metadata(grant_metadata(grant, active_policy_version=active_policy.version))


@router.delete("/mcp/grants/{grant_id}")
async def revoke_grant_endpoint(
    grant_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    grant = await revoke_approval_grant(
        db,
        grant_id=grant_id,
        actor="master-key",
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Approval grant not found")
    active_policy = await load_active_policy(db, settings)
    return _json_metadata(grant_metadata(grant, active_policy_version=active_policy.version))


@router.get("/mcp/policies")
async def policy_inventory(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    active = await load_active_policy(db, settings)
    stored = await list_policy_versions(db)
    stored_versions = {item.version for item in stored}
    items = [
        _json_metadata(
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
        )
        for item in stored
    ]
    for version, document in settings.mcp_policies.items():
        if version not in stored_versions:
            items.append(
                {
                    "version": version,
                    "status": "active"
                    if active.source == "configuration" and active.version == version
                    else "configured",
                    "source": "configuration",
                    "document": document,
                    "diff_from_active": policy_diff(active.document, document),
                }
            )
    activations = await list_policy_activations(db)
    return {
        "active": {"version": active.version, "source": active.source, "document": active.document},
        "items": items,
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


@router.post("/mcp/policies/validate")
async def validate_policy_endpoint(
    body: MCPPolicyDocumentRequest,
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


@router.post("/mcp/policies/drafts", status_code=201)
async def create_policy_draft_endpoint(
    body: MCPPolicyDraftCreateRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    policy = await create_policy_draft(
        db,
        version=body.version,
        document=body.document,
        base_version=body.base_version,
        actor="master-key",
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
            "created_at": policy.created_at,
        }
    )


@router.post("/mcp/policies/{version}/activate")
async def activate_policy_endpoint(
    version: str,
    body: MCPPolicyActivationRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    policy = await activate_policy_version(
        db,
        version=version,
        actor="master-key",
        reason=body.reason,
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
        settings=settings,
    )
    return _json_metadata({"version": policy.version, "status": policy.status, "activated_at": policy.activated_at})


@router.post("/mcp/policies/simulate")
async def simulate_policy_endpoint(
    body: MCPPolicySimulationRequest,
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
        version, document, source = f"simulation:{active.version}", body.document, "inline"
    elif body.version:
        snapshot = await load_policy_version(db, settings, body.version)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Policy version not found")
        version, document, source = snapshot.version, snapshot.document, snapshot.source
    else:
        snapshot = await load_active_policy(db, settings)
        version, document, source = snapshot.version, snapshot.document, snapshot.source
    identity = ResolvedIdentity(
        user_id=body.user_id,
        team_id=body.team_id,
        key_id=None,
        scopes=body.scopes,
    )
    decision = MCPPolicyEngine(settings, policy_version=version, policy_document=document).authorize(
        identity, body.server, body.tool, body.arguments
    )
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
        "effective_action": "allow" if decision.action == "require_approval" and grant else decision.action,
        "reason": decision.reason,
        "rule_name": decision.rule_name,
        "policy_version": decision.policy_version,
        "policy_source": source,
        "constraints": decision.constraints or {},
        "grant_offer": decision.grant,
        "standing_grant": grant_metadata(grant, active_policy_version=decision.policy_version) if grant else None,
    }


def _json_metadata(value: dict):
    return {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in value.items()}
