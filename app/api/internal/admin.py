"""Admin endpoints for usage reporting and user/key management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin
from app.db.engine import get_db
from app.db.repositories.usage import get_leaderboard, get_usage_summary
from app.db.repositories.users import (
    create_api_key,
    create_team,
    create_user,
    get_user_by_external_id,
)

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


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
    db: AsyncSession = Depends(get_db),
):
    raw_key, api_key = await create_api_key(db, user_id=user_id, name=name)
    return {
        "key": raw_key,  # shown once
        "key_prefix": api_key.key_prefix,
        "id": api_key.id,
    }
