---
title: Analytics & observability
description: Usage analytics with PostgreSQL materialized views, Langfuse trace export, and Prometheus metrics.
---

Three complementary observability systems cover different granularities:

| System | Granularity | Use case |
|---|---|---|
| PostgreSQL materialized views | Daily aggregates | Cost attribution, team leaderboards, billing |
| Langfuse | Per-request traces | Debugging, prompt quality, latency analysis |
| Prometheus | Real-time counters/histograms | Alerting, dashboards, SLO tracking |

## PostgreSQL usage data

Every Relay-key inference request commits a `UsageRecord` and `AuditLog` together. The audit event includes request ID,
user/team identity, model, status, token counts, RAG use, and the number of scrubbed PII entities. Prompt and response
content is not copied into this audit row.

Credential lifecycle events are also durable: key creation, revocation, and rotation record the actor, key prefix,
target user, and replacement relationship without logging raw keys or hashes.

A PostgreSQL materialized view (`usage_daily`) pre-aggregates these by `(day, user_id, team_id, model)` and is refreshed hourly in the background.

### Query via admin API

```bash
# Usage for a specific user over the last 7 days
curl "http://localhost:8000/internal/usage?user_id=user_01j...&since=2025-01-01" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

```bash
# Team leaderboard — top token consumers this month
curl "http://localhost:8000/internal/usage/leaderboard?dimension=team&metric=tokens&since=2025-01-01" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

### Direct SQL

```sql
-- Daily cost by team, last 30 days
SELECT
  day,
  team_id,
  SUM(cost_usd) AS total_cost,
  SUM(total_tokens) AS total_tokens
FROM usage_daily
WHERE day >= NOW() - INTERVAL '30 days'
GROUP BY day, team_id
ORDER BY day DESC, total_cost DESC;
```

## Langfuse

Langfuse provides per-request traces with prompt/completion content, token counts, latency, and cost.

### Enable

```yaml
analytics:
  enabled: true
  provider: langfuse
```

Set credentials:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=          # empty = Langfuse Cloud; set for self-hosted
```

Helm:

```yaml
secrets:
  langfusePublicKey: "pk-lf-..."
  langfuseSecretKey: "sk-lf-..."
  langfuseHost: ""
```

### Self-hosted Langfuse

The `docker-compose.yml` includes a Langfuse stack:

```bash
docker compose --profile langfuse up -d
```

Then set `LANGFUSE_HOST=http://langfuse:3000`.

## Prometheus

All key proxy metrics are exposed at `/metrics` in Prometheus text format. The endpoint requires the master key by
default. See [Health & metrics](/docs/api-reference/health) for authentication and the full metric list.

Recommended alerts:

```yaml
# Alert: high rate limit hit rate
- alert: HighRateLimitHits
  expr: rate(relay_rate_limit_hits_total[5m]) > 1
  for: 5m

# Alert: p95 latency over 5s
- alert: HighLatency
  expr: histogram_quantile(0.95, sum by (le) (rate(relay_request_latency_seconds_bucket[5m]))) > 5
  for: 10m

# Alert: upstream errors
- alert: UpstreamErrors
  expr: sum(rate(relay_requests_total{status="error"}[5m])) > 0.1
  for: 2m
```

### Grafana dashboard

A suggested panel layout:

1. **Request rate** — `rate(relay_requests_total[5m])` by model
2. **Latency p50/p95/p99** — histogram quantiles
3. **Token throughput** — `rate(relay_tokens_total[5m])` split by `token_type`
4. **Rate limit hits** — `rate(relay_rate_limit_hits_total[5m])` by limit type
5. **Cache hit rate** — `rate(relay_cache_hits_total[5m]) / rate(relay_requests_total[5m])`
6. **PII entities scrubbed** — `rate(relay_pii_entities_scrubbed_total[5m])`
7. **Daily cost** (from PostgreSQL or Langfuse via data source plugin)
