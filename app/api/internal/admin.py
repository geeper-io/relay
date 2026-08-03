"""Admin endpoints for usage reporting and user/key management."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.roles import (
    AdminRole,
    list_admin_identities,
    list_admin_roles,
    remove_admin_role,
    set_admin_role,
)
from app.core.auth import require_admin
from app.db.engine import get_db
from app.db.models import AdminRoleAssignment, ApiKey, User
from app.db.repositories.usage import get_leaderboard, get_usage_summary
from app.db.repositories.users import (
    create_api_key,
    create_team,
    create_user,
    get_user_by_external_id,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
)

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


class AdminRoleRequest(BaseModel):
    role: AdminRole


def _admin_role_metadata(assignment: AdminRoleAssignment) -> dict:
    return {
        "user_id": assignment.user_id,
        "role": assignment.role,
        "assigned_by": assignment.assigned_by,
        "created_at": assignment.created_at,
        "updated_at": assignment.updated_at,
    }


@router.get("/admin-roles")
async def list_admin_roles_endpoint(
    role: AdminRole | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    assignments = await list_admin_roles(db, role=role, limit=limit)
    return {"items": [_admin_role_metadata(item) for item in assignments]}


@router.get("/admin-identities")
async def list_admin_identities_endpoint(
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
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


@router.put("/admin-roles/{user_id}")
async def set_admin_role_endpoint(
    user_id: str,
    body: AdminRoleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    assignment = await set_admin_role(
        db,
        user_id=user_id,
        role=body.role,
        actor="master-key",
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    return _admin_role_metadata(assignment)


@router.delete("/admin-roles/{user_id}", status_code=204)
async def remove_admin_role_endpoint(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    removed = await remove_admin_role(
        db,
        user_id=user_id,
        actor="master-key",
        request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Admin role assignment not found")


@router.get("/usage")
async def usage_report(
    user_id: str | None = None,
    team_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    granularity: Literal["day", "week", "month", "year"] | None = None,
    group_by: Literal["model", "user", "team"] = "model",
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate usage records.

    **Without** `granularity` — totals per `group_by` value over the window.

    **With** `granularity` — one row per `(period, group_by)` pair, ordered by
    period ascending. Useful for time-series charts.

    Examples:
    - Daily token burn by model:  `?granularity=day&group_by=model`
    - Monthly cost per team:      `?granularity=month&group_by=team`
    - This week per user:         `?granularity=day&group_by=user&since=...`
    """
    return await get_usage_summary(
        db,
        user_id=user_id,
        team_id=team_id,
        since=since,
        until=until,
        granularity=granularity,
        group_by=group_by,
        limit=limit,
    )


@router.get("/usage/leaderboard")
async def usage_leaderboard(
    dimension: Literal["user", "team", "model"] = "user",
    metric: Literal["cost_usd", "total_tokens", "requests"] = "cost_usd",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Top-N entities ranked by a metric over a time window.

    Examples:
    - Top 10 users by cost this month:    `?dimension=user&metric=cost_usd&since=2026-03-01`
    - Top 5 teams by token usage:         `?dimension=team&metric=total_tokens&limit=5`
    - Most-used models this week:         `?dimension=model&metric=requests&since=2026-03-10`
    """
    return await get_leaderboard(
        db,
        dimension=dimension,
        metric=metric,
        since=since,
        until=until,
        limit=limit,
    )


@router.post("/teams")
async def create_team_endpoint(
    name: str,
    tpm_limit: int = 500_000,
    daily_token_limit: int = 5_000_000,
    db: AsyncSession = Depends(get_db),
):
    team = await create_team(db, name=name, tpm_limit=tpm_limit, daily_token_limit=daily_token_limit)
    return {"id": team.id, "name": team.name}


@router.get("/users")
async def get_user_endpoint(
    external_id: str,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_external_id(db, external_id=external_id)
    if not user:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="User not found")
    return {"id": user.id, "external_id": user.external_id, "team_id": user.team_id}


@router.post("/users")
async def create_user_endpoint(
    external_id: str,
    team_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await create_user(db, external_id=external_id, team_id=team_id)
    return {"id": user.id, "external_id": user.external_id}


@router.post("/api-keys")
async def create_api_key_endpoint(
    user_id: str,
    name: str = "default",
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    db: AsyncSession = Depends(get_db),
):
    raw_key, api_key = await create_api_key(
        db,
        user_id=user_id,
        name=name,
        scopes=scopes,
        expires_at=expires_at,
        actor="admin",
    )
    return {
        "key": raw_key,  # shown once
        "key_prefix": api_key.key_prefix,
        "id": api_key.id,
        "scopes": api_key.scopes,
        "expires_at": api_key.expires_at,
    }


def _key_metadata(api_key: ApiKey) -> dict:
    expires_at = api_key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not api_key.is_active:
        status = "revoked"
    elif expires_at is not None and expires_at <= datetime.now(timezone.utc):
        status = "expired"
    else:
        status = "active"
    return {
        "id": api_key.id,
        "key_prefix": api_key.key_prefix,
        "user_id": api_key.user_id,
        "name": api_key.name,
        "scopes": api_key.scopes or [],
        "status": status,
        "is_active": api_key.is_active,
        "expires_at": api_key.expires_at,
        "last_used_at": api_key.last_used_at,
        "created_at": api_key.created_at,
    }


@router.get("/api-keys")
async def list_api_keys_endpoint(
    user_id: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    keys = await list_api_keys(
        db,
        user_id=user_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    return {"items": [_key_metadata(key) for key in keys], "limit": limit, "offset": offset}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key_endpoint(
    key_id: str,
    db: AsyncSession = Depends(get_db),
):
    api_key = await revoke_api_key(db, key_id=key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return _key_metadata(api_key)


@router.post("/api-keys/{key_id}/rotate")
async def rotate_api_key_endpoint(
    key_id: str,
    expires_at: datetime | None = None,
    preserve_expiry: bool = True,
    db: AsyncSession = Depends(get_db),
):
    result = await rotate_api_key(
        db,
        key_id=key_id,
        expires_at=expires_at,
        preserve_expiry=preserve_expiry,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Active API key not found")
    raw_key, new_key, old_key = result
    return {"key": raw_key, **_key_metadata(new_key), "rotated_from": old_key.id}
