"""PostgreSQL materialized view for pre-aggregated daily usage stats.

On SQLite (dev) this module is a no-op — queries run directly against
usage_records, which is fast enough for development data volumes.

On PostgreSQL (prod) the view is created at startup and refreshed hourly
by a background task in main.py:

    CREATE MATERIALIZED VIEW usage_daily AS
    SELECT date_trunc('day', ...), user_id, team_id, model, SUM(...) ...

The unique index on (day, user_id, team_id, model) enables
REFRESH MATERIALIZED VIEW CONCURRENTLY so refreshes don't block reads.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_engine

log = logging.getLogger(__name__)

_CREATE_VIEW = text("""
CREATE MATERIALIZED VIEW IF NOT EXISTS usage_daily AS
SELECT
    date_trunc('day', created_at)                           AS day,
    user_id,
    COALESCE(team_id, '__none__')                           AS team_id,
    model,
    COUNT(*)                                                AS requests,
    SUM(prompt_tokens)                                      AS prompt_tokens,
    SUM(completion_tokens)                                  AS completion_tokens,
    SUM(total_tokens)                                       AS total_tokens,
    SUM(cost_usd)                                           AS cost_usd,
    SUM(CASE WHEN cache_hit  THEN 1 ELSE 0 END)             AS cache_hits,
    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)       AS errors,
    AVG(latency_ms)                                         AS avg_latency_ms
FROM usage_records
GROUP BY 1, 2, 3, 4
""")

# CONCURRENTLY requires a unique index
_CREATE_UNIQUE_IDX = text("""
CREATE UNIQUE INDEX IF NOT EXISTS ix_usage_daily_pk
ON usage_daily (day, user_id, team_id, model)
""")

_CREATE_IDX_DAY   = text("CREATE INDEX IF NOT EXISTS ix_usage_daily_day  ON usage_daily (day)")
_CREATE_IDX_TEAM  = text("CREATE INDEX IF NOT EXISTS ix_usage_daily_team ON usage_daily (team_id, day)")
_CREATE_IDX_USER  = text("CREATE INDEX IF NOT EXISTS ix_usage_daily_user ON usage_daily (user_id, day)")

_REFRESH = text("REFRESH MATERIALIZED VIEW CONCURRENTLY usage_daily")


def _is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


async def ensure_analytics_view() -> None:
    """Create the materialized view and indexes if they don't exist yet.

    Called once at startup. Safe to call multiple times (IF NOT EXISTS).
    """
    if not _is_postgres():
        return

    async with get_engine().begin() as conn:
        await conn.execute(_CREATE_VIEW)
        await conn.execute(_CREATE_UNIQUE_IDX)
        await conn.execute(_CREATE_IDX_DAY)
        await conn.execute(_CREATE_IDX_TEAM)
        await conn.execute(_CREATE_IDX_USER)

    log.info("usage_daily materialized view ready")


async def refresh_analytics_view() -> None:
    """Refresh the materialized view. Called by the hourly background task."""
    if not _is_postgres():
        return

    async with get_engine().begin() as conn:
        await conn.execute(_REFRESH)

    log.info("usage_daily refreshed")
