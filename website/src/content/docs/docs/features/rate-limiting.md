---
title: Rate limiting
description: Token-bucket rate limiting per user and per team with memory and Redis backends.
---

## How it works

Geeper Relay uses a **token-bucket** algorithm. Each user has three independent buckets:

| Bucket | Config key | Default |
|---|---|---|
| Requests per minute | `defaults.requests_per_minute` | 60 |
| Tokens per minute | `defaults.tokens_per_minute` | 100 000 |
| Tokens per day | `defaults.tokens_per_day` | 1 000 000 |

Each team can optionally have:

| Bucket | Set via |
|---|---|
| Team tokens per minute | `POST /internal/teams` → `tpm_limit` |
| Team tokens per day | `POST /internal/teams` → `daily_token_limit` |

A request consumes from both the user bucket **and** the team bucket. Either one can reject the request.

## Configuration

```yaml
rate_limiting:
  enabled: true
  backend: memory   # or "redis"
  defaults:
    requests_per_minute: 60
    tokens_per_minute: 100000
    tokens_per_day: 1000000
```

## Backends

### Memory (default)

Buckets are stored in-process. Fast (no network round-trip), but:
- Not shared across uvicorn workers within the same process (rare issue with `--workers > 1`)
- Not shared across replicas — each pod enforces limits independently

Suitable for single-replica deployments and local development.

### Redis

```yaml
rate_limiting:
  backend: redis
```

Buckets are stored in Redis with atomic Lua scripts. Shared across all workers and all replicas.

:::tip
When `redis.enabled: true` in the Helm chart, the proxy automatically switches to the Redis backend. No manual config change needed.
:::

Connect to an external Redis:

```bash
RATE_LIMITING__REDIS_URL=redis://user:pass@redis.internal:6379
```

## Per-team overrides

Override limits for a specific team via the admin API:

```bash
curl -X POST http://localhost:8000/internal/teams \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -d '{
    "name": "data-science",
    "tpm_limit": 500000,
    "daily_token_limit": 10000000
  }'
```

The `tpm_limit` and `daily_token_limit` fields are optional — omit to use the global defaults for that team's users.

## Rate limit responses

When a bucket is exhausted the proxy returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 47
Content-Type: application/json

{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Token rate limit exceeded. Retry after 47 seconds.",
    "code": 429
  }
}
```

`Retry-After` is the number of seconds until the bucket refills enough to allow the request.

## Prometheus metrics

```
llm_proxy_rate_limit_hits_total{limit_type="requests_per_minute"} 3
llm_proxy_rate_limit_hits_total{limit_type="tokens_per_minute"} 12
llm_proxy_rate_limit_hits_total{limit_type="tokens_per_day"} 1
```
