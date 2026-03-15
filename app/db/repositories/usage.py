from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Integer, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_engine, get_session_factory
from app.db.models import UsageRecord


# ── Write ─────────────────────────────────────────────────────────────────────

async def record_usage(
    *,
    user_id: str,
    team_id: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    request_id: str,
    cost_usd: float = 0.0,
    cache_hit: bool = False,
    was_rag_used: bool = False,
    pii_entities_scrubbed: int = 0,
    status: str = "success",
    error_code: str | None = None,
) -> UsageRecord:
    async with get_session_factory()() as db:
        record = UsageRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            team_id=team_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
            request_id=request_id,
            cost_usd=cost_usd,
            cache_hit=cache_hit,
            was_rag_used=was_rag_used,
            pii_entities_scrubbed=pii_entities_scrubbed,
            status=status,
            error_code=error_code,
        )
        db.add(record)
        await db.commit()
        return record


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


def _date_bucket(granularity: str):
    """Return a SQLAlchemy column expression that truncates created_at."""
    col = UsageRecord.created_at
    if _is_postgres():
        return func.date_trunc(granularity, col)
    # SQLite fallback
    fmt = {"day": "%Y-%m-%d", "week": "%Y-%W", "month": "%Y-%m", "year": "%Y"}[granularity]
    return func.strftime(fmt, col)


_GROUP_COL = {
    "model": UsageRecord.model,
    "user":  UsageRecord.user_id,
    "team":  UsageRecord.team_id,
}

_METRIC_COL = {
    "cost_usd":     func.sum(UsageRecord.cost_usd),
    "total_tokens": func.sum(UsageRecord.total_tokens),
    "requests":     func.count(UsageRecord.id),
}


# ── Queries ───────────────────────────────────────────────────────────────────

async def get_usage_summary(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    team_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    granularity: Literal["day", "week", "month", "year"] | None = None,
    group_by: Literal["model", "user", "team"] = "model",
    limit: int = 500,
) -> dict:
    """Aggregate usage records.

    - granularity=None  → one row per group_by value (totals)
    - granularity=day   → one row per (period, group_by value)
    """
    group_col = _GROUP_COL.get(group_by, UsageRecord.model)

    select_cols = [
        group_col.label("group"),
        func.sum(UsageRecord.prompt_tokens).label("prompt_tokens"),
        func.sum(UsageRecord.completion_tokens).label("completion_tokens"),
        func.sum(UsageRecord.total_tokens).label("total_tokens"),
        func.sum(UsageRecord.cost_usd).label("cost_usd"),
        func.count(UsageRecord.id).label("requests"),
        func.sum(UsageRecord.cache_hit.cast(Integer)).label("cache_hits"),
        func.sum(
            (UsageRecord.status == "error").cast(Integer)
        ).label("errors"),
        func.avg(UsageRecord.latency_ms).label("avg_latency_ms"),
    ]
    group_by_cols = [group_col]

    if granularity:
        bucket = _date_bucket(granularity).label("period")
        select_cols.insert(0, bucket)
        group_by_cols.insert(0, bucket)

    q = select(*select_cols).group_by(*group_by_cols).limit(limit)

    if user_id:
        q = q.where(UsageRecord.user_id == user_id)
    if team_id:
        q = q.where(UsageRecord.team_id == team_id)
    if since:
        q = q.where(UsageRecord.created_at >= since)
    if until:
        q = q.where(UsageRecord.created_at < until)

    if granularity:
        q = q.order_by(bucket)

    result = await db.execute(q)
    rows = result.all()

    def _row(r) -> dict:
        d: dict = {
            group_by: str(r.group) if r.group else None,
            "prompt_tokens":    r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens":     r.total_tokens or 0,
            "cost_usd":         round(r.cost_usd or 0.0, 6),
            "requests":         r.requests or 0,
            "cache_hits":       r.cache_hits or 0,
            "errors":           r.errors or 0,
            "avg_latency_ms":   round(r.avg_latency_ms or 0.0, 1),
        }
        if granularity:
            d["period"] = r.period.isoformat() if hasattr(r.period, "isoformat") else str(r.period)
        return d

    return {"rows": [_row(r) for r in rows]}


async def get_leaderboard(
    db: AsyncSession,
    *,
    dimension: Literal["user", "team", "model"] = "user",
    metric: Literal["cost_usd", "total_tokens", "requests"] = "cost_usd",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
) -> dict:
    """Return top-N entities ranked by a metric over a time window."""
    dim_col  = _GROUP_COL[dimension]
    agg_expr = _METRIC_COL[metric].label("value")

    q = (
        select(
            dim_col.label("dimension"),
            agg_expr,
            func.count(UsageRecord.id).label("requests"),
            func.sum(UsageRecord.cost_usd).label("cost_usd"),
            func.sum(UsageRecord.total_tokens).label("total_tokens"),
        )
        .group_by(dim_col)
        .order_by(desc("value"))
        .limit(limit)
    )

    if since:
        q = q.where(UsageRecord.created_at >= since)
    if until:
        q = q.where(UsageRecord.created_at < until)

    result = await db.execute(q)
    rows = result.all()

    return {
        "dimension": dimension,
        "metric":    metric,
        "rows": [
            {
                "rank":         i + 1,
                dimension:      str(r.dimension) if r.dimension else None,
                "value":        round(r.value or 0.0, 6),
                "requests":     r.requests or 0,
                "cost_usd":     round(r.cost_usd or 0.0, 6),
                "total_tokens": r.total_tokens or 0,
            }
            for i, r in enumerate(rows)
        ],
    }
