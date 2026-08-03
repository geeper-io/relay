# Configuration reference

All settings live in `config/config.yaml`. Any value can be overridden with an environment variable using `__` as the
nesting separator (e.g. `RAG__ENABLED=false`).

## LLM providers

```yaml
llm:
  default_model: "gpt-4o"

  # Models users are allowed to request
  allowed_models:
    - "gpt-4o"
    - "gpt-4o-mini"
    - "claude-3-5-sonnet-20241022"
    - "claude-haiku-4-5-20251001"
    - "azure/gpt-4o"

  # Friendly aliases (e.g. old name → new name)
  model_aliases:
    gpt-4: "gpt-4o"

  # Hard cap on output tokens per model
  per_model_max_tokens:
    gpt-4o: 8192

  # Tried in order when the primary model is unavailable or hits a context-window limit
  fallback_models:
    - "claude-3-5-sonnet-20241022"
    - "gpt-4o-mini"
```

Provider keys go in `.env`:

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-deployment.openai.azure.com
```

## Database migrations

Relay uses Alembic migrations for its relational schema. Startup runs the compatibility migration wrapper before
initializing any services:

```bash
python -m app.db.migrate upgrade
```

The command reads `DATABASE_URL`, upgrades fresh or already-versioned databases to the current head, and safely adopts
unversioned databases created by older Relay versions. Legacy adoption checks for complete historical schema stages;
it refuses to stamp a partial or unrelated database. Existing rows are preserved.

Concurrent Relay processes serialize upgrades with a PostgreSQL advisory lock or a SQLite lock file. For controlled
production rollouts, run the command once as a deployment migration step before starting the new application version;
the startup invocation then becomes a verified no-op. Inspect status with:

```bash
python -m app.db.migrate current
```

Direct `alembic upgrade head`, `alembic current`, and `alembic check` are available for fresh or already-versioned
databases. Use the Relay wrapper for the first migration of an older unversioned installation. Back up production data
before downgrading; downgrade operations can remove tables and data.

## Security defaults and API-key scopes

Relay starts with provider-key passthrough disabled and API documentation disabled. `/metrics` requires the master key,
and CORS is disabled unless explicit origins are configured:

```yaml
server:
  allow_passthrough_keys: false
  expose_docs: false
  metrics_require_auth: true
  cors_allowed_origins:
    - https://ai-portal.example.com
```

Set a strong `PROXY_MASTER_KEY`; startup fails for missing or known placeholder values. Relay API keys support these
scopes:

- `chat` — `/v1/chat/completions`, `/v1/messages`, and `/v1/models`
- `embeddings` — `/v1/embeddings`
- `rag:repo:owner/name` — retrieve from one repository
- `rag:*` — retrieve from any indexed repository
- `*` — all API capabilities

Scopes and an optional expiry can be assigned when creating a key:

```bash
curl -X POST 'https://relay.internal/internal/api-keys?user_id=USER_ID&scopes=chat&scopes=rag%3Arepo%3Amyorg%2Fbackend&expires_at=2026-12-31T23%3A59%3A59Z' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## PII scrubbing

```yaml
pii:
  enabled: true
  score_threshold: 0.7   # Presidio confidence threshold (0–1)
  entities:
    - PERSON
    - EMAIL_ADDRESS
    - PHONE_NUMBER
    - CREDIT_CARD
    - US_SSN
    - IP_ADDRESS
    - LOCATION
    - NRP
    - MEDICAL_LICENSE
    - INTERNAL_SECRET      # high-confidence provider/GitHub/Bearer token patterns; never restored
  allow_list:            # exact strings that are never scrubbed (case-insensitive)
    - Settings           # e.g. class names that Presidio mis-detects as person names
    - Config
    - Manager
```

Custom regex patterns (employee IDs, internal project codes, etc.) are defined in `app/pii/regex_patterns.py`. Add a
`PatternRecognizer` entry there and include its entity name in `pii.entities` to activate it.

PII is replaced with request-local, typed placeholders
(`<<PII_EMAIL_ADDRESS_8e841b7a95114eb4af19496c6f20a86c>>`) before the request reaches the LLM.
Placeholders are swapped back in the response. The same original value always maps to the same placeholder within a
request, so the LLM can still reason about relationships between entities.

System/developer instructions, ordinary message text, tool-call arguments, Responses API function arguments, code
blocks, and git diffs are all scrubbed. Use `allow_list` for known product or class names that the NER model
misclassifies; Relay does not bypass the provider boundary based on content format.

Retrieved knowledge-base text follows a stricter rule: detected PII is replaced with irreversible
`<<REDACTED_ENTITY>>` markers, so internal-document values are never added to the caller's restoration map.
`INTERNAL_SECRET` matches are also irreversible for caller-originated content and are never restored into output.
Retrieved context matching an active `content_policy.blocked_patterns` expression is dropped rather than sent to the
provider or used to reject the caller's otherwise-valid request.

## RAG / knowledge base

```yaml
rag:
  enabled: true
  top_k: 5                          # chunks returned per query
  score_threshold: 0.75             # cosine distance; 0 = identical, 1 = orthogonal
                                    # 0.75 is tuned for all-MiniLM-L6-v2 on mixed code + doc corpora
  embedding_model: "all-MiniLM-L6-v2"   # runs locally, no API key needed
  require_acl: true                       # derive repository filters from API-key scopes
  hybrid_enabled: true                    # dense + BM25-style lexical candidates
  candidate_multiplier: 4                # candidate pool relative to top_k
  rrf_k: 60                               # reciprocal-rank-fusion smoothing constant
  dense_weight: 1.0
  lexical_weight: 1.0
  reranker_model: ""                     # optional local/pinned CrossEncoder model
  reranker_top_n: 20
  context_max_tokens: 4000                # hard budget for formatted source chunks
```

Supported file formats: `.txt`, `.md`, `.rst` (word-based chunking) and `.py`, `.js`, `.ts`, `.go`, `.rb`, `.java`,
`.rs`, `.c`, `.cpp`, `.cs`, `.php`, `.swift`, `.kt`, `.scala`, `.sh` (AST-aware chunking via tree-sitter — each
top-level function and class becomes its own chunk).

**Uploading individual files (requires admin key):**

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $MASTER_KEY" \
  -F "file=@docs/handbook.md"
# → {"filename": "handbook.md", "chunks_ingested": 14}
```

**Scoping queries to a repository:**

Grant the API key `rag:repo:owner/repo`, then pass `X-Relay-Repo: owner/repo` to narrow retrieval. The header never
grants access: Relay rejects repositories absent from the authenticated key's scopes. Without the header, retrieval
searches only the repositories authorized by the key. Keys without RAG scopes receive no knowledge-base context.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gr-..." \
  -H "X-Relay-Repo: myorg/backend" \
  -d '{"model": "gpt-4o", "messages": [{"role":"user","content":"How does auth work?"}]}'
```

**Debugging retrieval:**

```bash
# Production hybrid search — shows dense, lexical, fused, and optional reranker scores
curl "http://localhost:8000/internal/kb/search?q=authentication+middleware&repo=myorg/backend" \
  -H "Authorization: Bearer $MASTER_KEY"
```

## Code review / repo sync

Relay can index entire GitHub or GitLab repositories and keep them up to date incrementally. Each sync:

1. Fetches the HEAD commit SHA — skips everything if it matches the stored cursor.
2. On first run: full tree index.
3. On subsequent runs: only changed, added, and removed files (via the compare API).
4. Saves the cursor only when all files succeed — partial syncs retry from the same point.

```yaml
code_review:
  sync_on_startup: true   # set false when using the sync CronJob

  github:
    token: ""             # PAT with `repo` (read) scope; omit for public repos
    ref: main
    include:              # explicit allowlist — only these repos are indexed
      - myorg/backend
      - myorg/frontend
    orgs:                 # auto-discover all repos in these orgs (ignored if include is set)
      - myorg
    exclude:              # blacklist applied after include/discovery
      - myorg/archived-monolith

  gitlab:
    token: ""             # PAT with `read_repository` scope
    host: https://gitlab.com
    ref: main
    include:              # numeric project IDs or URL-encoded paths
      - "123"
      - "mygroup%2Fbackend"
    groups:               # auto-discover all projects in these groups
      - mygroup
```

Environment variables:

```env
CODE_REVIEW__GITHUB__TOKEN=ghp_...
CODE_REVIEW__GITLAB__TOKEN=glpat-...
```

**Manual sync / force re-index via API:**

```bash
# Incremental sync (skips if already up-to-date)
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "github", "repo": "myorg/backend", "token": "ghp_...", "ref": "main"}'

# Force full re-index (ignores stored SHA)
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "github", "repo": "myorg/backend", "token": "ghp_...", "force": true}'
```

Sync runs in the background and returns immediately. Check `/internal/kb/stats` for chunk count.

**Kubernetes CronJob:**

The Helm chart includes an optional sync worker that runs independently of the proxy pod:

```yaml
# values.yaml
syncJob:
  enabled: true
  schedule: "0 * * * *"   # hourly
```

Set `code_review.sync_on_startup: false` to prevent the proxy pods from also syncing on boot.

**Code review workflow:**

```bash
# Review uncommitted changes against the indexed codebase
git diff | jq -Rs '{
  model: "gpt-4o",
  messages: [{
    role: "user",
    content: ("Review this diff against our codebase conventions:\n\n" + .)
  }]
}' | curl -s http://localhost:8000/v1/chat/completions \
     -H "Authorization: Bearer gr-..." \
     -H "Content-Type: application/json" \
     -H "X-Relay-Repo: myorg/backend" \
     -d @- | jq -r '.choices[0].message.content'
```

**How the pipeline handles a code review request:**

1. **PII scrubbing** — the diff is scanned like every other provider-bound text field; known false positives should
   be handled with `pii.allow_list`.
2. **RAG** — the diff is the retrieval query. API-key scopes authorize repositories; `X-Relay-Repo` can narrow that
   authorized set but cannot expand it. The top-K matching functions, classes, and docs are appended after application
   instructions inside an explicit untrusted-reference boundary. Retrieved PII is irreversibly redacted.
3. **LLM call** — the model receives the diff plus the retrieved context and returns a review grounded in your actual
   codebase rather than generic advice.

## Rate limiting

```yaml
rate_limiting:
  enabled: true
  backend: "memory"      # "memory" (single process) | "redis" (multi-worker)
  defaults:
    requests_per_minute: 60
    tokens_per_minute: 100000
    tokens_per_day: 1000000
```

Per-user overrides are set on the `User` DB record (`rpm_limit`, `tpm_limit`). Team TPM and daily limits come from the
`Team` record. User and team minute/day checks are performed atomically. The memory backend is process-local; the Redis
backend uses one atomic Lua operation and is required for multiple workers or replicas.

Relay reserves prompt tokens before the provider call, then reconciles actual prompt and completion tokens after the
response, including streams. Completion overages block subsequent requests.

For multi-worker deployments set `backend: "redis"` and provide `RATE_LIMITING__REDIS_URL`.

## Response caching

```yaml
cache:
  enabled: true
  type: "local"    # "local" | "redis"
  ttl: 3600        # seconds
```

Exact-match only — the full message list must be identical for a cache hit. Streaming responses are not cached. When a
cached response is served:

- The upstream LLM is not called
- Tokens and cost are recorded as 0 in usage records
- Response includes `X-Cache-Hit: true` header

For multi-worker deployments use `type: "redis"`.

## Content policy

```yaml
content_policy:
  enabled: true
  max_input_tokens: 32000
  blocked_patterns:
    - "ignore previous instructions"
    - "jailbreak"
```

Requests matching any pattern (case-insensitive) are rejected with HTTP 400 before reaching the LLM.

## Anthropic Messages API (`/v1/messages`)

The proxy exposes a native Anthropic Messages API endpoint alongside the OpenAI-compatible one. Any client that uses the
Anthropic SDK or speaks the Anthropic wire format will work without translation.

```bash
curl http://localhost:8000/v1/messages \
  -H "Authorization: Bearer gr-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Summarise the onboarding docs"}]
  }'
```

Supported features: `system` prompt, multi-turn messages, tool use, streaming (Anthropic SSE event format),
`stop_sequences`. The full request pipeline (PII scrubbing, RAG, rate limiting, caching, usage tracking, metrics) runs
identically on both endpoints.

**Streaming** emits proper Anthropic SSE events:

```
event: message_start
event: content_block_start
event: ping
event: content_block_delta   ← repeated for each text chunk
event: content_block_stop
event: message_delta
event: message_stop
```

## MCP gateway

Relay exposes a stable MCP Streamable HTTP endpoint at `POST /mcp` and REST endpoints under `/v1/mcp`. It brokers
configured remote MCP servers; it does not run local `stdio` processes or execute tool code inside Relay.

```yaml
mcp:
  enabled: true
  public_url: "https://relay.example.com/mcp"
  protocol_version: "2025-11-25"
  delegated_grant_ttl_seconds: 300
  active_policy_version: "2026-07-30"
  servers:
    code:
      url: "https://code-tools.internal/mcp"
      headers_env:
        Authorization: CODE_MCP_AUTHORIZATION
  policies:
    "2026-07-30":
      default_action: deny
      rules:
        - server: code
          tool: "test_*"
          action: allow
        - server: code
          tool: execute
          action: require_approval
          grant:
            subject: user
            ttl_seconds: 28800
            max_calls: 20
            constraints:
              allowed_values:
                runtime: [python]
```

Keys use granular `mcp:*`, `mcp:server:*`, or `mcp:server:tool` scopes. Approval tokens are short-lived, signed,
argument-bound, policy-bound, and single-use. Remote arguments are checked against JSON Schema 2020-12; results are
PII-scrubbed and size-limited. See [Responses API with Relay MCP](mcp-responses.md) for the complete delegated
credential, approval, and continuation flow.

`require_approval` rules can offer a bounded standing `grant` after the first human approval. `subject` is `user` or
`team`; a team offer falls back to the requesting user when no team is present. `ttl_seconds` is limited to 60 seconds
through 30 days and `max_calls` to 1 through 10,000. Optional `tool_pattern`, `constraints`, `workflow_id`, and `reason`
further describe or narrow the grant. Grants match only the active policy version and never override scopes, policy
denials, or the rule's argument constraints.

Configured policy versions bootstrap the MCP control plane. Once an admin activates a database-backed draft, the
database active pointer takes precedence over `active_policy_version` so every replica observes the same policy without
a configuration rollout. Drafts are immutable and validated before activation; rollback reactivates an earlier
version. Keep the configured active version available as a recovery baseline. Approvals and standing grants never
cross policy-version boundaries.

### MCP tools in the Responses API

Relay can publish selected configured servers to a model as one native remote MCP tool. The model provider receives a
short-lived `grmcp-...` credential rather than the caller's Relay API key. Before a policy-protected tool runs, the
Responses API pauses with an `mcp_approval_request`; Relay creates its own durable approval bound to the exact server,
tool, arguments hash, caller, and policy version.
This follows OpenAI's [remote MCP tool and approval protocol](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).

```json
POST /v1/responses
{
  "model": "gpt-4o",
  "input": "Run the unit tests",
  "store": true,
  "relay_mcp_servers": ["code"],
  "relay_mcp_purpose": "Validate the current change"
}
```

The approval item includes Relay metadata:

```json
{
  "type": "mcp_approval_request",
  "id": "mcpr_...",
  "name": "code__execute",
  "server_label": "relay",
  "relay_approval": {"id": "<relay-approval-id>", "status": "pending"}
}
```

An administrator decides that Relay approval through `POST /internal/mcp/approvals/<relay-approval-id>/decision`. The
caller then continues using the standard Responses API item:

```json
POST /v1/responses
{
  "model": "gpt-4o",
  "previous_response_id": "resp_...",
  "store": true,
  "input": [{
    "type": "mcp_approval_response",
    "approval_request_id": "mcpr_...",
    "approve": true
  }]
}
```

Relay verifies the durable decision and replaces the discovery credential with a short-lived grant restricted to that
single approved call. The gateway consumes the approval when the provider invokes the tool, preventing replay or
argument substitution. `authorization` is regenerated on every Responses request and is never returned to the caller.

This initial integration requires `store: true`, `stream: false`, and foreground mode because continuation state lives
in the provider's Responses conversation. Relay rejects other combinations explicitly. Set `mcp.public_url` to the
provider-reachable HTTPS URL of Relay's `/mcp` endpoint; local HTTP is accepted only when `allow_insecure_http` is
enabled for development.

## Admin dashboard

The optional dashboard at `/admin` provides a focused MCP approval inbox. It is disabled by default.

```yaml
admin:
  enabled: true
  session_ttl_seconds: 28800
  secure_cookies: true
  oidc_enabled: true
  bootstrap_emails: [relay-admin@example.com]
  allow_master_key_login: true
```

OIDC-backed sessions support durable `viewer`, `approver`, and `admin` roles. `bootstrap_emails` provides initial admin
access; role assignments are then managed through `/internal/admin-roles`. The optional master-key login remains a
break-glass path and can be disabled independently. Keep secure cookies enabled with HTTPS in production. See
[Admin dashboard](admin-dashboard.md) for the security model and operating guidance.

## Self-service developer portal

Employees sign in with general OIDC or the Google compatibility configuration and land at `/portal`. The portal shows
their effective user/team limits, recent requests, tokens, cost and model usage, and safe API-key metadata. Users can
create, rotate, and revoke only their own keys and copy setup instructions for OpenAI-compatible clients, Anthropic,
Claude Code, remote MCP, and Responses API tool workflows.

```
GET /auth/login      → redirect to the identity provider
GET /auth/callback   → exchange code, create user session, redirect to /portal
```

The callback no longer creates a key on every login. Users create purpose-specific keys in the portal; raw secrets are
shown once. The legacy one-shot page remains temporarily available through `/auth/login?issue_key=true`.

```yaml
portal:
  enabled: true
  session_ttl_seconds: 28800
  secure_cookies: true
  max_active_keys: 10
  max_key_ttl_days: 365
```

Self-service key scopes must be a subset of `oidc.default_key_scopes`; users cannot grant themselves broader access.
The active-key count and maximum TTL are enforced server-side. Sessions are signed, HttpOnly, SameSite Strict, and all
key mutations require a session-bound CSRF token. Keep secure cookies enabled outside local HTTP development.

**Setup:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → Create OAuth 2.0
   Client ID (Web application).
2. Add `https://your-proxy.internal/auth/callback` to the list of authorised redirect URIs.
3. Set the credentials in `.env` or `config.yaml`:

```env
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
AUTH_BASE_URL=https://your-proxy.internal
```

```yaml
# config/config.yaml
google_client_id: "xxxx.apps.googleusercontent.com"
google_client_secret: "GOCSPX-..."
auth_base_url: "https://your-proxy.internal"
```

If `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are not set both routes return `501 Not Implemented`. The portal can be
disabled entirely by simply not providing those credentials.

Set `portal.enabled: false` to disable the user surface while retaining the explicit legacy key flow.

## Langfuse analytics

```yaml
analytics:
  enabled: false
  provider: "langfuse"
```

```env
ANALYTICS__ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000  # omit for Langfuse Cloud
```

Each request creates a Langfuse trace with `user_id`, `session_id` (= `X-Request-Id`, so multi-turn conversations group
correctly), model tags, cost, and whether RAG was used.

**Self-hosted Langfuse:**

```bash
# Start Langfuse alongside the proxy
docker compose -f docker/docker-compose.yml up langfuse postgres -d

# Open http://localhost:3000, create an account and a project,
# copy the keys into .env, then restart the proxy.
```
