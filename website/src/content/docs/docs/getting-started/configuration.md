---
title: Configuration reference
description: Full reference for config.yaml — all settings with defaults and Helm equivalents.
---

The proxy reads a YAML config file on startup. The path is set via the `CONFIG_FILE` environment variable (default: `config/config.yaml`). In Kubernetes the file is mounted from a ConfigMap generated from `values.yaml`.

## `server`

| Key | Type | Default | Description |
|---|---|---|---|
| `workers` | int | `4` | Number of uvicorn worker processes |
| `log_level` | string | `"info"` | Log level: `debug`, `info`, `warning`, `error` |

Helm: `config.workers`, `config.logLevel`

## `llm`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_model` | string | `"gpt-4o"` | Model used when none is specified in the request |
| `allowed_models` | list | see below | Requests for any other model are rejected with 400 |
| `fallback_models` | list | `[]` | Tried in order when the primary model returns an error |
| `model_aliases` | map | `{}` | e.g. `gpt-4: gpt-4o` — rewrite model names before routing |
| `per_model_max_tokens` | map | `{}` | Override max output tokens per model |

Default `allowed_models`:
```yaml
- gpt-4o
- gpt-4o-mini
- claude-3-5-sonnet-20241022
- claude-3-haiku-20240307
```

Helm: `config.llm.*`

## `rag`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable RAG context injection |
| `top_k` | int | `5` | Maximum chunks to retrieve |
| `score_threshold` | float | `0.4` | Minimum cosine similarity score |
| `embedding_model` | string | `"all-MiniLM-L6-v2"` | sentence-transformers model for embedding |

Helm: `config.rag.*`

## `pii`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable PII detection and scrubbing |
| `score_threshold` | float | `0.7` | Minimum Presidio confidence score to redact |
| `entities` | list | see below | Entity types to detect |

Default entities: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`, `US_SSN`, `IP_ADDRESS`, `LOCATION`

Helm: `config.pii.*`

## `rate_limiting`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable rate limiting |
| `backend` | string | `"memory"` | `memory` or `redis`. Auto-set to `redis` when `redis.enabled=true` in Helm |
| `defaults.requests_per_minute` | int | `60` | Per-user req/min limit |
| `defaults.tokens_per_minute` | int | `100000` | Per-user tokens/min limit |
| `defaults.tokens_per_day` | int | `1000000` | Per-user tokens/day limit |

Per-team limits are set via the admin API — see [Teams & API keys](/docs/admin/teams-and-keys).

Helm: `config.rateLimiting.*`

## `content_policy`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable content policy checks |
| `max_input_tokens` | int | `32000` | Reject requests with more prompt tokens than this |
| `blocked_patterns` | list | see below | Literal strings (case-insensitive) to block |

Default blocked patterns:
```yaml
- "ignore previous instructions"
- "ignore all previous"
- "jailbreak"
```

Helm: `config.contentPolicy.*`

## `cache`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable response caching |
| `type` | string | `"local"` | `local` (in-process dict) or `redis`. Auto-set to `redis` when `redis.enabled=true` in Helm |
| `ttl` | int | `3600` | Cache TTL in seconds |

Helm: `config.cache.*`

## `analytics`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable Langfuse trace export |
| `provider` | string | `"langfuse"` | Only `langfuse` supported currently |

Langfuse credentials are set via environment variables: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (empty = Langfuse Cloud).

Helm: `config.analytics.*`, `secrets.langfuse*`

## Complete example

```yaml
server:
  workers: 4
  log_level: info

llm:
  default_model: gpt-4o
  allowed_models:
    - gpt-4o
    - gpt-4o-mini
    - claude-3-5-sonnet-20241022
  fallback_models: []
  model_aliases:
    gpt-4: gpt-4o
  per_model_max_tokens:
    gpt-4o: 8192

rag:
  enabled: true
  top_k: 5
  score_threshold: 0.4
  embedding_model: all-MiniLM-L6-v2

pii:
  enabled: true
  score_threshold: 0.7
  entities:
    - PERSON
    - EMAIL_ADDRESS
    - PHONE_NUMBER
    - CREDIT_CARD
    - US_SSN
    - IP_ADDRESS
    - LOCATION

rate_limiting:
  enabled: true
  backend: memory
  defaults:
    requests_per_minute: 60
    tokens_per_minute: 100000
    tokens_per_day: 1000000

content_policy:
  enabled: true
  max_input_tokens: 32000
  blocked_patterns:
    - "ignore previous instructions"
    - "ignore all previous"
    - "jailbreak"

cache:
  enabled: false
  type: local
  ttl: 3600

analytics:
  enabled: false
  provider: langfuse
```
