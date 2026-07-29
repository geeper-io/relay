---
title: Rate limiting
description: Atomic minute and daily budgets per user and team with memory and Redis backends.
---

## How it works

Relay checks five limits as one admission decision. Each user has:

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

A request consumes from both user and team budgets. Either can reject it. Prompt tokens are reserved before the
provider call; actual prompt plus completion usage is reconciled afterward, including streamed responses. An overage
therefore blocks subsequent requests instead of letting long completions escape accounting.

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

Minute limits use in-process token buckets and daily usage uses UTC-day counters. This is fast, but:
- Not shared across uvicorn workers within the same process (rare issue with `--workers > 1`)
- Not shared across replicas — each pod enforces limits independently

Suitable for single-replica deployments and local development.

### Redis

```yaml
rate_limiting:
  backend: redis
```

Redis uses fixed minute/UTC-day keys. One Lua script checks and increments user RPM, user TPM/day, and team TPM/day
atomically, so a rejected request does not partially consume another budget. State is shared by every worker and pod.

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
curl -X POST \
  'http://localhost:8000/internal/teams?name=data-science&tpm_limit=500000&daily_token_limit=10000000' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

The team values are optional. Without them, Relay uses five times the global per-user TPM/day defaults.

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
relay_rate_limit_hits_total{limit_type="general"} 16
```

The HTTP response identifies the exhausted budget; the current Prometheus counter aggregates rate-limit rejections
under `limit_type="general"`.
