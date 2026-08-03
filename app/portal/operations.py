"""User-scoped queries for the self-service developer portal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.operations import get_admin_user_detail
from app.config import Settings
from app.db.models import ApiKey, UsageRecord


def key_metadata(key: ApiKey) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    status = "revoked" if not key.is_active else "expired" if expires_at and expires_at <= now else "active"
    return {
        "id": key.id,
        "key_prefix": key.key_prefix,
        "name": key.name,
        "scopes": key.scopes or [],
        "status": status,
        "expires_at": key.expires_at,
        "last_used_at": key.last_used_at,
        "created_at": key.created_at,
    }


async def get_portal_overview(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    days: int,
) -> dict | None:
    detail = await get_admin_user_detail(db, settings, user_id=user_id, days=days)
    if detail is None or not detail["is_active"]:
        return None

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute = now - timedelta(minutes=1)
    daily_tokens = await db.scalar(
        select(func.sum(UsageRecord.total_tokens)).where(
            UsageRecord.user_id == user_id,
            UsageRecord.created_at >= today,
        )
    )
    minute_usage = (
        await db.execute(
            select(
                func.count(UsageRecord.id),
                func.sum(UsageRecord.total_tokens),
            ).where(
                UsageRecord.user_id == user_id,
                UsageRecord.created_at >= minute,
            )
        )
    ).one()
    team_daily_tokens = 0
    if detail["team_id"]:
        team_daily_tokens = int(
            await db.scalar(
                select(func.sum(UsageRecord.total_tokens)).where(
                    UsageRecord.team_id == detail["team_id"],
                    UsageRecord.created_at >= today,
                )
            )
            or 0
        )

    detail["limits"].update(
        {
            "tokens_today": int(daily_tokens or 0),
            "requests_last_minute": int(minute_usage[0] or 0),
            "tokens_last_minute": int(minute_usage[1] or 0),
            "team_tokens_today": team_daily_tokens,
        }
    )
    detail["allowed_key_scopes"] = settings.oidc__default_key_scopes
    detail["max_active_keys"] = settings.portal__max_active_keys
    return detail


async def get_owned_key(db: AsyncSession, *, user_id: str, key_id: str) -> ApiKey | None:
    return await db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id))
