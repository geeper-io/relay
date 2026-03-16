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
```

Custom regex patterns (employee IDs, internal project codes, etc.) are defined in `app/pii/regex_patterns.py`. Add a
`PatternRecognizer` entry there — no config change needed.

PII is replaced with typed placeholders (`<<PII_EMAIL_ADDRESS_a3f2b1>>`) before the request reaches the LLM.
Placeholders are swapped back in the response. The same original value always maps to the same placeholder within a
request, so the LLM can still reason about relationships between entities.

## RAG / knowledge base

```yaml
rag:
  enabled: true
  top_k: 5               # chunks returned per query
  score_threshold: 0.4   # cosine distance threshold — lower = more similar
  embedding_model: "all-MiniLM-L6-v2"  # local, no API key needed
```

**Ingesting documents:**

```bash
# Ingest everything in knowledge_base/
python scripts/ingest_kb.py

# Ingest a specific file
python scripts/ingest_kb.py path/to/document.md
```

Supported formats: `.txt`, `.md`, `.rst`. Drop files into `knowledge_base/` and re-run the script. Chunks are stored in
ChromaDB at `./chroma_data` (configurable via `CHROMA_PERSIST_DIR`).

You can also ingest via the API (requires admin key):

```bash
# Upload a file
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $MASTER_KEY" \
  -F "file=@docs/handbook.md"

# Ingest a server-side directory
curl -X POST "http://localhost:8000/internal/kb/ingest-directory?directory=knowledge_base" \
  -H "Authorization: Bearer $MASTER_KEY"

# Check stats
curl http://localhost:8000/internal/kb/stats \
  -H "Authorization: Bearer $MASTER_KEY"
```

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

Per-user overrides are set on the `User` DB record (`rpm_limit`, `tpm_limit`). Teams have a shared TPM bucket (default:
5× the per-user limit).

For multi-worker deployments set `backend: "redis"` and provide `REDIS_URL`.

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

## Google SSO portal

Employees can self-serve an API key by logging in with their Google account — no admin intervention required.

```
GET /auth/login      → redirect to Google consent screen
GET /auth/callback   → exchange code, create user, issue key, show HTML page
```

The callback page displays the generated `gr-...` key once with a copy button and the two shell commands needed to
configure Claude Code.

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

Each login issues a **fresh** API key (`name: sso`). Old keys remain valid until revoked via the admin API, so
accidental re-logins do not break running sessions.

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
