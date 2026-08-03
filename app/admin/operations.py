"""Read-only operational queries for the browser admin console."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ApiKey, MCPApproval, Team, UsageRecord, User


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _day_bucket(db: AsyncSession):
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        return func.date_trunc("day", UsageRecord.created_at)
    return func.strftime("%Y-%m-%d", UsageRecord.created_at)


def _usage_totals(row) -> dict:
    requests = int(row.requests or 0)
    cache_hits = int(row.cache_hits or 0)
    errors = int(row.errors or 0)
    return {
        "requests": requests,
        "total_tokens": int(row.total_tokens or 0),
        "cost_usd": round(float(row.cost_usd or 0), 6),
        "errors": errors,
        "error_rate": round(errors / requests, 4) if requests else 0,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0,
        "avg_latency_ms": round(float(row.avg_latency_ms or 0), 1),
    }


def _is_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now


def _period(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


async def get_admin_overview(
    db: AsyncSession,
    *,
    days: int,
) -> dict:
    since = _since(days)
    now = datetime.now(timezone.utc)
    totals = (
        await db.execute(
            select(
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
                func.sum((UsageRecord.status == "error").cast(Integer)).label("errors"),
                func.sum(UsageRecord.cache_hit.cast(Integer)).label("cache_hits"),
                func.avg(UsageRecord.latency_ms).label("avg_latency_ms"),
            ).where(UsageRecord.created_at >= since)
        )
    ).one()

    user_counts = (
        await db.execute(
            select(
                func.count(User.id).label("total"),
                func.sum(User.is_active.cast(Integer)).label("enabled"),
            )
        )
    ).one()
    active_users = await db.scalar(
        select(func.count(func.distinct(UsageRecord.user_id))).where(UsageRecord.created_at >= since)
    )
    team_count = await db.scalar(select(func.count(Team.id)))
    pending_approvals = await db.scalar(
        select(func.count(MCPApproval.id)).where(
            MCPApproval.status == "pending",
            MCPApproval.expires_at > now,
        )
    )

    bucket = _day_bucket(db).label("period")
    daily_rows = (
        await db.execute(
            select(
                bucket,
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
                func.sum((UsageRecord.status == "error").cast(Integer)).label("errors"),
            )
            .where(UsageRecord.created_at >= since)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()

    top_models = (
        await db.execute(
            select(
                UsageRecord.model,
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
            )
            .where(UsageRecord.created_at >= since)
            .group_by(UsageRecord.model)
            .order_by(desc("cost_usd"))
            .limit(5)
        )
    ).all()
    top_users = (
        await db.execute(
            select(
                User.id,
                User.external_id,
                Team.name.label("team_name"),
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
            )
            .join(UsageRecord, UsageRecord.user_id == User.id)
            .outerjoin(Team, Team.id == User.team_id)
            .where(UsageRecord.created_at >= since)
            .group_by(User.id, User.external_id, Team.name)
            .order_by(desc("cost_usd"))
            .limit(5)
        )
    ).all()

    return {
        "window_days": days,
        "since": since,
        "totals": _usage_totals(totals),
        "users": {
            "total": int(user_counts.total or 0),
            "enabled": int(user_counts.enabled or 0),
            "active_in_window": int(active_users or 0),
        },
        "teams": int(team_count or 0),
        "pending_approvals": int(pending_approvals or 0),
        "daily": [
            {
                "period": _period(row.period),
                "requests": int(row.requests or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0), 6),
                "errors": int(row.errors or 0),
            }
            for row in daily_rows
        ],
        "top_models": [
            {
                "model": row.model,
                "requests": int(row.requests or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0), 6),
            }
            for row in top_models
        ],
        "top_users": [
            {
                "user_id": row.id,
                "external_id": row.external_id,
                "team_name": row.team_name,
                "requests": int(row.requests or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0), 6),
            }
            for row in top_users
        ],
    }


def _user_usage_subquery(since: datetime):
    return (
        select(
            UsageRecord.user_id.label("user_id"),
            func.count(UsageRecord.id).label("requests"),
            func.sum(UsageRecord.total_tokens).label("total_tokens"),
            func.sum(UsageRecord.cost_usd).label("cost_usd"),
            func.sum((UsageRecord.status == "error").cast(Integer)).label("errors"),
            func.avg(UsageRecord.latency_ms).label("avg_latency_ms"),
            func.max(UsageRecord.created_at).label("last_activity_at"),
        )
        .where(UsageRecord.created_at >= since)
        .group_by(UsageRecord.user_id)
        .subquery()
    )


def _key_inventory_subquery(now: datetime):
    active_key = and_(
        ApiKey.is_active.is_(True),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
    )
    return (
        select(
            ApiKey.user_id.label("user_id"),
            func.sum(case((active_key, 1), else_=0)).label("active_keys"),
            func.count(ApiKey.id).label("total_keys"),
            func.max(ApiKey.last_used_at).label("last_key_used_at"),
        )
        .group_by(ApiKey.user_id)
        .subquery()
    )


def _user_row(row, settings: Settings) -> dict:
    return {
        "id": row.id,
        "external_id": row.external_id,
        "team_id": row.team_id,
        "team_name": row.team_name,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "limits": {
            "rpm": row.rpm_limit or settings.default_rpm,
            "tpm": row.tpm_limit or settings.default_tpm,
            "tokens_per_day": settings.default_tpd,
            "rpm_custom": row.rpm_limit is not None,
            "tpm_custom": row.tpm_limit is not None,
            "team_tpm": row.team_tpm_limit,
            "team_tokens_per_day": row.team_daily_token_limit,
        },
        "usage": {
            "requests": int(row.requests or 0),
            "total_tokens": int(row.total_tokens or 0),
            "cost_usd": round(float(row.cost_usd or 0), 6),
            "errors": int(row.errors or 0),
            "avg_latency_ms": round(float(row.avg_latency_ms or 0), 1),
            "last_activity_at": row.last_activity_at,
        },
        "keys": {
            "active": int(row.active_keys or 0),
            "total": int(row.total_keys or 0),
            "last_used_at": row.last_key_used_at,
        },
    }


async def list_admin_users(
    db: AsyncSession,
    settings: Settings,
    *,
    query: str | None,
    days: int,
    limit: int,
    offset: int,
    user_id: str | None = None,
) -> dict:
    since = _since(days)
    now = datetime.now(timezone.utc)
    usage = _user_usage_subquery(since)
    keys = _key_inventory_subquery(now)
    columns = (
        User.id,
        User.external_id,
        User.team_id,
        User.is_active,
        User.created_at,
        User.rpm_limit,
        User.tpm_limit,
        Team.name.label("team_name"),
        Team.tpm_limit.label("team_tpm_limit"),
        Team.daily_token_limit.label("team_daily_token_limit"),
        usage.c.requests,
        usage.c.total_tokens,
        usage.c.cost_usd,
        usage.c.errors,
        usage.c.avg_latency_ms,
        usage.c.last_activity_at,
        keys.c.active_keys,
        keys.c.total_keys,
        keys.c.last_key_used_at,
    )
    base = (
        select(*columns)
        .outerjoin(Team, Team.id == User.team_id)
        .outerjoin(usage, usage.c.user_id == User.id)
        .outerjoin(keys, keys.c.user_id == User.id)
    )
    count_query = select(func.count(User.id)).outerjoin(Team, Team.id == User.team_id)
    normalized = (query or "").strip()
    if user_id:
        base = base.where(User.id == user_id)
        count_query = count_query.where(User.id == user_id)
    elif normalized:
        pattern = f"%{normalized}%"
        predicate = or_(
            User.external_id.ilike(pattern),
            User.id == normalized,
            Team.name.ilike(pattern),
        )
        base = base.where(predicate)
        count_query = count_query.where(predicate)
    rows = (
        await db.execute(
            base.order_by(func.coalesce(usage.c.last_activity_at, User.created_at).desc()).limit(limit).offset(offset)
        )
    ).all()
    total = await db.scalar(count_query)
    return {
        "items": [_user_row(row, settings) for row in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "window_days": days,
    }


async def get_admin_user_detail(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    days: int,
) -> dict | None:
    listing = await list_admin_users(
        db,
        settings,
        query=user_id,
        days=days,
        limit=1,
        offset=0,
        user_id=user_id,
    )
    if not listing["items"] or listing["items"][0]["id"] != user_id:
        return None

    since = _since(days)
    bucket = _day_bucket(db).label("period")
    daily_rows = (
        await db.execute(
            select(
                bucket,
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
            )
            .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= since)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    model_rows = (
        await db.execute(
            select(
                UsageRecord.model,
                func.count(UsageRecord.id).label("requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
            )
            .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= since)
            .group_by(UsageRecord.model)
            .order_by(desc("cost_usd"))
        )
    ).all()
    keys = (
        await db.scalars(select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc()).limit(100))
    ).all()
    now = datetime.now(timezone.utc)
    return {
        **listing["items"][0],
        "window_days": days,
        "daily": [
            {
                "period": _period(row.period),
                "requests": int(row.requests or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0), 6),
            }
            for row in daily_rows
        ],
        "models": [
            {
                "model": row.model,
                "requests": int(row.requests or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0), 6),
            }
            for row in model_rows
        ],
        "api_keys": [
            {
                "id": key.id,
                "key_prefix": key.key_prefix,
                "name": key.name,
                "scopes": key.scopes or [],
                "status": (
                    "revoked" if not key.is_active else "expired" if _is_expired(key.expires_at, now) else "active"
                ),
                "expires_at": key.expires_at,
                "last_used_at": key.last_used_at,
                "created_at": key.created_at,
            }
            for key in keys
        ],
    }
