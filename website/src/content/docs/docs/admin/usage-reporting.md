---
title: Usage reporting
description: Query token usage, cost, and leaderboards via the admin API.
---

Usage data is stored in PostgreSQL and pre-aggregated into a daily materialized view (`usage_daily`) refreshed hourly.

## Query usage

```bash
curl "http://localhost:8000/internal/usage" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

### Query parameters

| Parameter | Type | Description |
|---|---|---|
| `user_id` | string | Filter by user |
| `team_id` | string | Filter by team |
| `since` | ISO datetime | Start of time range (e.g. `2025-01-01`) |
| `until` | ISO datetime | End of time range (default: now) |
| `granularity` | string | `day`, `month` (default: `day`) |

Example — usage for team `engineering` over the last 7 days:

```bash
curl "http://localhost:8000/internal/usage?team_id=team_01j...&since=2025-01-01" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "rows": [
    {
      "day": "2025-01-07",
      "team_id": "team_01j...",
      "model": "gpt-4o",
      "requests": 142,
      "prompt_tokens": 189400,
      "completion_tokens": 42100,
      "total_tokens": 231500,
      "cost_usd": 1.158
    }
  ],
  "total_cost_usd": 8.43,
  "total_tokens": 1840200
}
```

## Leaderboards

```bash
# Top users by token spend this month
curl "http://localhost:8000/internal/usage/leaderboard?dimension=user&metric=tokens&since=2025-01-01" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"

# Top teams by cost
curl "http://localhost:8000/internal/usage/leaderboard?dimension=team&metric=cost&since=2025-01-01" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

### Parameters

| Parameter | Values | Description |
|---|---|---|
| `dimension` | `user`, `team`, `model` | Group by dimension |
| `metric` | `tokens`, `cost`, `requests` | Sort metric |
| `since` | ISO datetime | Start of window |
| `limit` | int (default 10) | Maximum entries returned |

## Raw SQL

For custom reporting, query PostgreSQL directly:

```sql
-- Monthly cost by model, current year
SELECT
  DATE_TRUNC('month', day) AS month,
  model,
  SUM(cost_usd) AS cost,
  SUM(total_tokens) AS tokens
FROM usage_daily
WHERE day >= DATE_TRUNC('year', NOW())
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC;

-- Top 10 users by spend, all time
SELECT
  u.external_id,
  SUM(ud.cost_usd) AS total_cost,
  SUM(ud.total_tokens) AS total_tokens
FROM usage_daily ud
JOIN users u ON ud.user_id = u.id
GROUP BY u.external_id
ORDER BY total_cost DESC
LIMIT 10;
```

## Materialized view refresh

The view is refreshed every hour by a background task. On PostgreSQL, `REFRESH MATERIALIZED VIEW CONCURRENTLY` is used — reads are never blocked during refresh.

On SQLite (local dev), the materialized view concept is not used — queries run directly against `usage_records`.

To manually trigger a refresh (PostgreSQL):

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY usage_daily;
```
