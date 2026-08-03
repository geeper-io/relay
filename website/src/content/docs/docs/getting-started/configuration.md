---
title: Configuration reference
description: Full reference for config.yaml — all settings with defaults and Helm equivalents.
---

The proxy reads a YAML config file on startup. The path is set via the `CONFIG_FILE` environment variable (default: `config/config.yaml`). In Kubernetes the file is mounted from a ConfigMap generated from `values.yaml`.

## Database and migrations

Set `DATABASE_URL` to an async SQLAlchemy URL. Local development defaults to
`sqlite+aiosqlite:///./proxy.db`; production should use PostgreSQL with `postgresql+asyncpg://...`.

Relay applies versioned Alembic migrations at startup. You can run or inspect them explicitly before a rollout:

```bash
python -m app.db.migrate upgrade
python -m app.db.migrate current
```

Use this wrapper when upgrading an installation that predates Alembic. It recognizes complete legacy schema stages,
stamps the correct revision, and applies only missing migrations. Concurrent replicas serialize upgrades with a
database advisory lock. The wrapper refuses partial or unrelated schemas instead of guessing.

## `server`

| Key | Type | Default | Description |
|---|---|---|---|
| `workers` | int | `4` | Number of uvicorn worker processes |
| `log_level` | string | `"info"` | Log level: `debug`, `info`, `warning`, `error` |
| `allow_passthrough_keys` | bool | `false` | Accept non-`gr-` provider keys. Enable only for explicitly trusted BYOK deployments |
| `expose_docs` | bool | `false` | Expose `/docs`, `/redoc`, and `/openapi.json` |
| `metrics_require_auth` | bool | `true` | Require the master key on `/metrics` |
| `cors_allowed_origins` | list | `[]` | Browser origins allowed by CORS. Empty disables CORS middleware |

Helm: `config.workers`, `config.logLevel`, `config.allowPassthroughKeys`, `config.exposeDocs`,
`config.metricsRequireAuth`, `config.corsAllowedOrigins`

## `llm`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_model` | string | `"gpt-4o"` | Model used when none is specified in the request |
| `default_embedding_model` | string | `""` | Model used by `/v1/embeddings` when none is specified |
| `allowed_models` | list | see below | Requests for any other model are rejected with 400 |
| `fallback_models` | list | `[]` | Tried in order when the primary model returns an error |
| `model_aliases` | map | `{}` | e.g. `gpt-4: gpt-4o` — rewrite model names before routing |
| `per_model_max_tokens` | map | `{}` | Override max output tokens per model |
| `deployments` | map | `{}` | Logical alias → model, capabilities, and fallback chain |

Default `allowed_models`:
```yaml
- gpt-4o
- gpt-4o-mini
- anthropic/claude-sonnet-4-6
- anthropic/claude-haiku-4-5-20251001
```

Helm: `config.llm.*`

## `routing`

| Key | Type | Default | Description |
|---|---|---|---|
| `active_policy_version` | string | `"default"` | Policy snapshot applied to new requests |
| `require_declared_capabilities` | bool | `false` | Reject direct/undeclared models when capabilities are requested |
| `policies` | map | `{}` | Versioned deployment allowlists, capability rules, routes, and team overrides |

See [Deployment and policy routing](/docs/features/routing).

## `responses`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_store` | bool | `false` | Default provider-side storage behavior for `/v1/responses` |

## `mcp`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable the MCP protocol and REST gateway endpoints |
| `protocol_version` | string | `"2025-11-25"` | MCP version offered during initialization |
| `servers` | map | `{}` | Registered remote Streamable HTTP servers and credential environment mappings |
| `active_policy_version` | string | `"default"` | Active MCP authorization policy snapshot |
| `policies` | map | `{}` | Ordered versioned allow, deny, and approval rules |
| `approval_ttl_seconds` | int | `900` | Lifetime of pending and approved operations |
| `request_timeout_seconds` | float | `60` | Per-request remote MCP timeout |
| `max_result_bytes` | int | `1000000` | Maximum sanitized tool-result size |
| `allow_insecure_http` | bool | `false` | Permit non-TLS remote MCP URLs; development only |
| `allowed_origins` | list | `[]` | Browser origins accepted by `POST /mcp` when an Origin header is present |

See [MCP gateway and approvals](/docs/features/mcp-gateway).

## `telemetry`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable OpenTelemetry FastAPI and HTTPX instrumentation |
| `service_name` | string | `"geeper-relay"` | OTLP resource service name |
| `otlp_endpoint` | string | `""` | OTLP/HTTP traces endpoint |
| `otlp_headers` | map | `{}` | OTLP exporter headers |
| `sample_ratio` | float | `1.0` | Parent-based trace sampling ratio |

## `oidc`

| Key | Type | Default | Description |
|---|---|---|---|
| `issuer_url` | string | `""` | OIDC issuer; enables discovery when credentials are present |
| `scopes` | list | `[openid,email,profile]` | Authorization request scopes |
| `require_verified_email` | bool | `true` | Require a verified-email claim for general OIDC |
| `allowed_email_domains` | list | `[]` | Optional sign-in domain allowlist |
| `default_key_scopes` | list | `[chat,responses]` | Maximum scopes users may select for portal keys |
| `token_endpoint_auth_method` | string | `client_secret_post` | `client_secret_post` or `client_secret_basic` |

Client ID/secret should be supplied through `OIDC__CLIENT_ID` and `OIDC__CLIENT_SECRET` or the equivalent Helm Secret.

## `portal`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable the SSO-backed developer portal at `/portal` |
| `session_ttl_seconds` | int | `28800` | Signed browser-session lifetime |
| `secure_cookies` | bool | `true` | Send the portal cookie only over HTTPS |
| `max_active_keys` | int | `10` | Maximum active self-service keys per user |
| `max_key_ttl_days` | int | `365` | Longest expiry users may choose |

## `rag`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable RAG context injection |
| `top_k` | int | `5` | Maximum chunks to retrieve |
| `score_threshold` | float | `0.75` | Maximum dense cosine distance |
| `embedding_model` | string | `"all-MiniLM-L6-v2"` | sentence-transformers model for embedding |
| `require_acl` | bool | `true` | Derive repository filters from authenticated API-key scopes |
| `hybrid_enabled` | bool | `true` | Fuse dense and BM25-style lexical candidate rankings |
| `candidate_multiplier` | int | `4` | Candidate pool size relative to `top_k` |
| `rrf_k` | int | `60` | Reciprocal-rank-fusion smoothing constant |
| `reranker_model` | string | `""` | Optional pinned/local sentence-transformers CrossEncoder |
| `reranker_top_n` | int | `20` | Fused candidates sent to the cross-encoder |
| `context_max_tokens` | int | `4000` | Maximum formatted retrieval-context tokens |

With ACL enforcement enabled, keys require `rag:repo:owner/name` for individual repositories or `rag:*` for all
repositories. `X-Relay-Repo` narrows access; it never grants access.

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
  allow_passthrough_keys: false
  expose_docs: false
  metrics_require_auth: true
  cors_allowed_origins: []

llm:
  default_model: anthropic/claude-haiku-4-5-20251001
  default_embedding_model: text-embedding-3-small
  allowed_models:
    - anthropic/claude-haiku-4-5-20251001
    - anthropic/claude-sonnet-4-6
  fallback_models: []
  model_aliases: {}
  per_model_max_tokens: {}
  deployments: {}

routing:
  active_policy_version: default
  require_declared_capabilities: false
  policies: {}

responses:
  default_store: false

mcp:
  enabled: false
  protocol_version: "2025-11-25"
  servers: {}
  active_policy_version: default
  policies: {}
  approval_ttl_seconds: 900
  request_timeout_seconds: 60
  max_result_bytes: 1000000
  allow_insecure_http: false
  allowed_origins: []

telemetry:
  enabled: false
  service_name: geeper-relay
  otlp_endpoint: ""
  otlp_headers: {}
  sample_ratio: 1.0

rag:
  enabled: true
  top_k: 5
  score_threshold: 0.4
  embedding_model: all-MiniLM-L6-v2
  require_acl: true

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
    - INTERNAL_SECRET

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
