---
title: Response caching
description: Exact-match response cache with local (in-process) and Redis backends.
---

Caching runs at **stage 07**. On a cache hit the response is returned immediately — the LLM is never called, and stages 08–09 are skipped entirely.

## Configuration

```yaml
cache:
  enabled: false   # disabled by default
  type: local      # "local" or "redis"
  ttl: 3600        # seconds
```

Enable it:

```yaml
cache:
  enabled: true
  type: local
  ttl: 3600
```

## Cache key

The cache key is a hash of:
- Normalised `messages` array (serialised JSON, sorted keys)
- `model` name

Two requests with identical messages and model always hit the same cache entry, regardless of which user sent them.

## Backends

### Local (in-process dict)

- Zero latency
- Not shared across workers or replicas
- Evicted on restart

Best for: single-replica development or deterministic demo workloads.

### Redis

```yaml
cache:
  type: redis
```

```bash
CACHE__REDIS_URL=redis://redis.internal:6379
```

- Shared across all workers and replicas
- Survives pod restarts
- `ttl` enforced via Redis `EXPIRE`

:::tip
When `redis.enabled: true` in the Helm chart, the proxy automatically uses the bundled Redis for caching (and rate limiting). No extra config needed.
:::

## When to use caching

Caching is most effective for:
- FAQ bots where the same questions recur frequently
- Documentation Q&A with a stable knowledge base
- CI/CD pipelines that run the same prompts repeatedly

Avoid caching for:
- Conversational agents (history varies every turn)
- Creative tasks where temperature > 0 is important
- Anything where freshness matters

## Limitations

- Streaming responses are **not** cached — only non-streaming requests
- Cache entries are per-request-shape only; there is no partial/semantic cache (i.e. a rephrased question always misses)
- No cache invalidation endpoint — entries expire naturally via TTL or flush Redis manually
