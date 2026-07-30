"""Administrative MCP approval queue endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.db.engine import get_db
from app.mcp.approvals import approval_metadata, decide_approval, list_approvals
from app.metrics import prometheus as metrics
from app.schemas.mcp import MCPApprovalDecisionRequest

router = APIRouter(tags=["mcp-admin"], dependencies=[Depends(require_admin)])


@router.get("/mcp/approvals")
async def approval_queue(
    status: str | None = "pending",
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    approvals = await list_approvals(db, status=status, limit=limit)
    return {"items": [_json_metadata(approval_metadata(item)) for item in approvals]}


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


def _json_metadata(value: dict):
    return {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in value.items()}
