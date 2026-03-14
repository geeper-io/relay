# LLM Proxy

An in-house AI gateway for teams that need controlled, observable access to LLMs (OpenAI, Claude, Azure, and others). Drop-in OpenAI API compatible — existing tools work without modification.

## Features

- **Multi-provider** — OpenAI, Anthropic, Azure OpenAI, and [any LiteLLM-supported provider](https://docs.litellm.ai/docs/providers)
- **Anthropic Messages API** — native `/v1/messages` endpoint so Claude Code and the Anthropic SDK connect without any adapter
- **Google SSO portal** — employees log in with Google at `/auth/login` and receive an API key on a self-serve HTML page; no admin involvement required
- **PII scrubbing** — strips personal data from requests before they leave your network using Microsoft Presidio (NLP-based) and custom regex patterns; restores placeholders in responses
- **RAG / internal knowledge base** — enriches answers with context from your internal docs (Markdown, text) via ChromaDB vector search
- **Usage tracking** — every request is logged with model, tokens, cost, latency, and user identity to a database
- **Prometheus metrics** — request count, latency, token usage, cost, cache hits, PII events, RAG hits, and rate limit events
- **Rate limiting** — per-user and per-team token/request budgets (in-memory or Redis)
- **Response caching** — exact-match cache via LiteLLM (local or Redis); `X-Cache-Hit: true` header on cache hits
- **Model fallbacks** — automatic failover to backup models on errors or context-window overflow
- **Content policy** — blocks prompt-injection patterns and oversized inputs
- **Langfuse analytics** — optional per-request LLM tracing with user IDs, session grouping, and cost (self-hosted or cloud)
- **Admin API** — manage users, teams, and API keys; pull usage reports

---

## Quick start

### Prerequisites

- Python 3.11+
- At least one LLM provider API key

### 1. Install

```bash
git clone <repo>
cd llm-proxy

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Download the spaCy NLP model used by Presidio for PII detection
python -m spacy download en_core_web_lg
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — at minimum set one provider key and a master key:

```env
OPENAI_API_KEY=sk-...
# or ANTHROPIC_API_KEY=sk-ant-...
PROXY_MASTER_KEY=your-secret-admin-key
```

### 3. Create your first user and API key

```bash
python scripts/create_api_key.py --external-id alice@company.com --team engineering
```

This prints a key like `llmp-...`. Save it — it is shown only once.

### 4. Run

```bash
uvicorn app.main:app --reload
```

The proxy is now listening on `http://localhost:8000`.

### 5. Make a request

Using curl:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer llmp-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "What is our parental leave policy?"}]
  }'
```

Using the OpenAI Python SDK (just change the `base_url`):

```python
from openai import OpenAI

client = OpenAI(
    api_key="llmp-...",
    base_url="http://localhost:8000/v1",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Summarise last quarter's results"}],
)
print(response.choices[0].message.content)
```

Using the **Anthropic Python SDK** or **Claude Code** via the native Messages API:

```python
import anthropic

client = anthropic.Anthropic(
    api_key="llmp-...",
    base_url="http://localhost:8000",
)

message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "What is our parental leave policy?"}],
)
print(message.content[0].text)
```

```bash
# Claude Code — point it at the proxy instead of Anthropic directly
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_AUTH_TOKEN=llmp-...
claude
```

---

## Kubernetes (Helm)

### Prerequisites

- Helm 3.x
- A Kubernetes cluster with a default StorageClass

### Install

```bash
# Add Bitnami repo (needed for PostgreSQL and Redis subcharts)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Fetch subchart dependencies
helm dependency build helm/llm-proxy

# Install (dry-run first to review)
helm upgrade --install llm-proxy helm/llm-proxy \
  --namespace llm-proxy --create-namespace \
  --set secrets.openaiApiKey=sk-... \
  --set secrets.anthropicApiKey=sk-ant-... \
  --set postgresql.auth.password=your-db-password
# PROXY_MASTER_KEY is auto-generated and stored in a separate secret.
```

### Production values file

Rather than `--set` flags, use a `values-prod.yaml` for production:

```yaml
# values-prod.yaml
replicaCount: 1   # see note below about scaling

image:
  repository: your-registry.example.com/llm-proxy
  tag: "1.2.3"

secrets:
  create: false
  existingSecret: llm-proxy-secrets   # pre-created via Vault, Sealed Secrets, etc.

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: llm-proxy.internal.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: llm-proxy-tls
      hosts:
        - llm-proxy.internal.example.com

resources:
  requests:
    cpu: "1"
    memory: 2Gi
  limits:
    cpu: "4"
    memory: 4Gi

postgresql:
  auth:
    existingSecret: llm-proxy-postgresql-secret
  primary:
    persistence:
      size: 50Gi

redis:
  enabled: true   # enables shared rate limiting + caching across workers

prometheus:
  serviceMonitor:
    enabled: true
    labels:
      release: prometheus   # match your Prometheus Operator selector
```

```bash
helm upgrade --install llm-proxy helm/llm-proxy \
  --namespace llm-proxy --create-namespace \
  -f values-prod.yaml
```

### Scaling beyond one replica

The embedded ChromaDB instance writes to the local filesystem (`/app/chroma_data`). Scaling to multiple replicas requires either:

- **ReadWriteMany storage** (NFS, EFS, Azure Files, GCS Fuse) — set `persistence.chroma.accessMode: ReadWriteMany` and pick a compatible `storageClass`
- **External ChromaDB server** — disable RAG (`config.rag.enabled: false`) or swap the vector store for a network-accessible alternative

For CPU-level concurrency without multiple replicas, increase `config.workers` (uvicorn processes within a single pod).

### Secret management

**`PROXY_MASTER_KEY`** lives in its own dedicated secret (`<release>-master-key`) and is never accepted as a plain-text value. On first install the chart generates a random 32-character key. On every subsequent `helm upgrade` the existing value is read back from the cluster via `lookup()` and reused — the key is never rotated unless you explicitly delete the secret. `helm uninstall` also leaves the secret behind (`helm.sh/resource-policy: keep`) so a reinstall picks it up unchanged.

To bring your own master key (Vault, Sealed Secrets, External Secrets Operator, etc.):

```yaml
secrets:
  existingMasterKeySecret: my-master-key-secret   # must contain key: PROXY_MASTER_KEY
```

To retrieve the auto-generated key:

```bash
kubectl get secret --namespace llm-proxy my-release-llm-proxy-master-key \
  -o jsonpath="{.data.PROXY_MASTER_KEY}" | base64 -d
```

**API keys** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) are in a separate secret. For production, manage them externally and set:

```yaml
secrets:
  create: false
  existingSecret: llm-proxy-api-keys
```

The external Secret must contain: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL` (if not using the bundled PostgreSQL), plus any optional keys (`GOOGLE_CLIENT_ID`, `LANGFUSE_PUBLIC_KEY`, etc.).

---

## Docker

```bash
cp .env.example .env  # fill in keys

# Build and start everything
docker compose -f docker/docker-compose.yml up -d

# Build the image alone (optional)
docker build -t llm-proxy .

# Run standalone (SQLite, no Postgres required)
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e PROXY_MASTER_KEY=secret \
  -v $(pwd)/chroma_data:/app/chroma_data \
  -v $(pwd)/knowledge_base:/app/knowledge_base \
  llm-proxy
```

Services started by compose: `proxy` (port 8000), `postgres`, `prometheus` (port 9090).

**Worker count** defaults to 4. Override with `-e WORKERS=8` or set `WORKERS=8` in `.env`.

---

## Configuration reference

All settings live in `config/config.yaml`. Any value can be overridden with an environment variable using `__` as the nesting separator (e.g. `RAG__ENABLED=false`).

### LLM providers

```yaml
llm:
  default_model: "gpt-4o"

  # Models users are allowed to request
  allowed_models:
    - "gpt-4o"
    - "gpt-4o-mini"
    - "claude-3-5-sonnet-20241022"
    - "claude-3-haiku-20240307"
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

### PII scrubbing

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

Custom regex patterns (employee IDs, internal project codes, etc.) are defined in `app/pii/regex_patterns.py`. Add a `PatternRecognizer` entry there — no config change needed.

PII is replaced with typed placeholders (`<<PII_EMAIL_ADDRESS_a3f2b1>>`) before the request reaches the LLM. Placeholders are swapped back in the response. The same original value always maps to the same placeholder within a request, so the LLM can still reason about relationships between entities.

### RAG / knowledge base

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

Supported formats: `.txt`, `.md`, `.rst`. Drop files into `knowledge_base/` and re-run the script. Chunks are stored in ChromaDB at `./chroma_data` (configurable via `CHROMA_PERSIST_DIR`).

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

### Rate limiting

```yaml
rate_limiting:
  enabled: true
  backend: "memory"      # "memory" (single process) | "redis" (multi-worker)
  defaults:
    requests_per_minute: 60
    tokens_per_minute: 100000
    tokens_per_day: 1000000
```

Per-user overrides are set on the `User` DB record (`rpm_limit`, `tpm_limit`). Teams have a shared TPM bucket (default: 5× the per-user limit).

For multi-worker deployments set `backend: "redis"` and provide `REDIS_URL`.

### Response caching

```yaml
cache:
  enabled: true
  type: "local"    # "local" | "redis"
  ttl: 3600        # seconds
```

Exact-match only — the full message list must be identical for a cache hit. Streaming responses are not cached. When a cached response is served:
- The upstream LLM is not called
- Tokens and cost are recorded as 0 in usage records
- Response includes `X-Cache-Hit: true` header

For multi-worker deployments use `type: "redis"`.

### Content policy

```yaml
content_policy:
  enabled: true
  max_input_tokens: 32000
  blocked_patterns:
    - "ignore previous instructions"
    - "jailbreak"
```

Requests matching any pattern (case-insensitive) are rejected with HTTP 400 before reaching the LLM.

### Anthropic Messages API (`/v1/messages`)

The proxy exposes a native Anthropic Messages API endpoint alongside the OpenAI-compatible one. Any client that uses the Anthropic SDK or speaks the Anthropic wire format will work without translation.

```bash
curl http://localhost:8000/v1/messages \
  -H "Authorization: Bearer llmp-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Summarise the onboarding docs"}]
  }'
```

Supported features: `system` prompt, multi-turn messages, tool use, streaming (Anthropic SSE event format), `stop_sequences`. The full request pipeline (PII scrubbing, RAG, rate limiting, caching, usage tracking, metrics) runs identically on both endpoints.

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

### Google SSO portal

Employees can self-serve an API key by logging in with their Google account — no admin intervention required.

```
GET /auth/login      → redirect to Google consent screen
GET /auth/callback   → exchange code, create user, issue key, show HTML page
```

The callback page displays the generated `llmp-...` key once with a copy button and the two shell commands needed to configure Claude Code.

**Setup:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → Create OAuth 2.0 Client ID (Web application).
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

If `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are not set both routes return `501 Not Implemented`. The portal can be disabled entirely by simply not providing those credentials.

Each login issues a **fresh** API key (`name: sso`). Old keys remain valid until revoked via the admin API, so accidental re-logins do not break running sessions.

### Langfuse analytics

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

Each request creates a Langfuse trace with `user_id`, `session_id` (= `X-Request-Id`, so multi-turn conversations group correctly), model tags, cost, and whether RAG was used.

**Self-hosted Langfuse:**

```bash
# Start Langfuse alongside the proxy
docker compose -f docker/docker-compose.yml up langfuse postgres -d

# Open http://localhost:3000, create an account and a project,
# copy the keys into .env, then restart the proxy.
```

---

## Admin API

All admin endpoints require `Authorization: Bearer <PROXY_MASTER_KEY>`.

### User and key management

```bash
# Create a team
curl -X POST "http://localhost:8000/internal/teams?name=engineering" \
  -H "Authorization: Bearer $MASTER_KEY"

# Create a user
curl -X POST "http://localhost:8000/internal/users?external_id=bob@company.com&team_id=<team-id>" \
  -H "Authorization: Bearer $MASTER_KEY"

# Issue an API key
curl -X POST "http://localhost:8000/internal/api-keys?user_id=<user-id>&name=laptop" \
  -H "Authorization: Bearer $MASTER_KEY"
# Returns: { "key": "llmp-...", "key_prefix": "llmp-XXXXXX", "id": "..." }
# The raw key is shown once and not stored.
```

### Usage reports and leaderboards

```bash
# Totals by model (default)
curl "http://localhost:8000/internal/usage" \
  -H "Authorization: Bearer $MASTER_KEY"

# Daily cost per team for the last 30 days
curl "http://localhost:8000/internal/usage?granularity=day&group_by=team&since=2026-02-10" \
  -H "Authorization: Bearer $MASTER_KEY"

# Monthly token burn by model
curl "http://localhost:8000/internal/usage?granularity=month&group_by=model" \
  -H "Authorization: Bearer $MASTER_KEY"

# This month, per user
curl "http://localhost:8000/internal/usage?granularity=day&group_by=user&since=2026-03-01" \
  -H "Authorization: Bearer $MASTER_KEY"
```

The `granularity` parameter (`day` | `week` | `month` | `year`) turns the response into a time series ordered by period. Without it you get flat totals.

Each row includes: `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`, `requests`, `cache_hits`, `errors`, `avg_latency_ms`.

**Leaderboards** — top-N entities ranked by a metric:

```bash
# Top 10 users by cost this month
curl "http://localhost:8000/internal/usage/leaderboard?dimension=user&metric=cost_usd&since=2026-03-01" \
  -H "Authorization: Bearer $MASTER_KEY"

# Top 5 teams by token usage
curl "http://localhost:8000/internal/usage/leaderboard?dimension=team&metric=total_tokens&limit=5" \
  -H "Authorization: Bearer $MASTER_KEY"

# Most requested models this week
curl "http://localhost:8000/internal/usage/leaderboard?dimension=model&metric=requests&since=2026-03-10" \
  -H "Authorization: Bearer $MASTER_KEY"
```

Parameters: `dimension` (`user` | `team` | `model`), `metric` (`cost_usd` | `total_tokens` | `requests`), `since`, `until`, `limit` (default 10).

```json
{
  "dimension": "user",
  "metric": "cost_usd",
  "rows": [
    { "rank": 1, "user": "alice-uuid", "value": 12.34, "requests": 345, "cost_usd": 12.34, "total_tokens": 980000 },
    { "rank": 2, "user": "bob-uuid",   "value":  8.10, "requests": 210, "cost_usd":  8.10, "total_tokens": 620000 }
  ]
}
```

**PostgreSQL only — materialized view**

On PostgreSQL, a `usage_daily` materialized view is created at startup and refreshed every hour in the background. It pre-aggregates all records by `(day, user, team, model)`, so leaderboard and time-series queries stay fast regardless of how many raw rows accumulate. On SQLite (dev) all queries run directly against `usage_records`.

---

## Prometheus metrics

Metrics are available at `http://localhost:8000/metrics`.

| Metric | Type | Description |
|--------|------|-------------|
| `llm_proxy_requests_total` | Counter | Total requests, labelled `model`, `status` |
| `llm_proxy_request_latency_seconds` | Histogram | End-to-end latency, labelled `model`, `stream` |
| `llm_proxy_tokens_total` | Counter | Tokens consumed, labelled `model`, `token_type` (`prompt`/`completion`) |
| `llm_proxy_cost_usd_total` | Counter | Cumulative USD cost, labelled `model` |
| `llm_proxy_cache_hits_total` | Counter | Cache hits, labelled `model` |
| `llm_proxy_pii_entities_scrubbed_total` | Counter | PII entities removed |
| `llm_proxy_pii_requests_affected_total` | Counter | Requests that contained PII |
| `llm_proxy_rag_retrievals_total` | Counter | RAG lookups, labelled `status` (`hit`/`miss`) |
| `llm_proxy_rag_chunks_retrieved` | Histogram | Chunks retrieved per request |
| `llm_proxy_rate_limit_hits_total` | Counter | Rate limit rejections, labelled `limit_type` |
| `llm_proxy_content_policy_blocks_total` | Counter | Content policy rejections |
| `llm_proxy_active_requests` | Gauge | Requests currently in flight |

Prometheus scrapes `proxy:8000/metrics` every 15 seconds (configured in `docker/prometheus.yml`).

---

## Project structure

```
app/
├── main.py                  # App factory and startup
├── config.py                # Settings (YAML + env vars)
├── api/
│   ├── v1/
│   │   ├── chat.py          # POST /v1/chat/completions  (OpenAI format)
│   │   ├── messages.py      # POST /v1/messages          (Anthropic format)
│   │   ├── models.py        # GET /v1/models
│   │   └── health.py        # GET /healthz  /readyz
│   ├── internal/
│   │   ├── admin.py         # User/team/key management, usage reports
│   │   └── kb.py            # Knowledge base ingestion endpoints
│   └── auth.py              # GET /auth/login  /auth/callback  (Google SSO)
├── core/
│   ├── auth.py              # API key → user/team resolution
│   ├── rate_limiter.py      # Token-bucket rate limiting
│   ├── content_policy.py    # Keyword blocking and token limits
│   └── exceptions.py        # Error types and HTTP handlers
├── pii/
│   ├── scrubber.py          # Presidio + regex → placeholder map
│   ├── restorer.py          # Placeholder restoration (streaming-safe)
│   └── regex_patterns.py    # Custom recognizers (employee IDs, etc.)
├── rag/
│   ├── embedder.py          # Local sentence-transformers embeddings
│   ├── vector_store.py      # ChromaDB client
│   ├── retriever.py         # Top-K retrieval and context formatting
│   └── ingestion.py         # Document chunking and upsert pipeline
├── llm/
│   └── client.py            # LiteLLM wrapper (token count, cost, fallbacks, cache)
├── schemas/
│   ├── openai.py            # Pydantic models — OpenAI chat format
│   └── anthropic.py         # Pydantic models — Anthropic Messages format + converters
├── analytics/
│   └── langfuse.py          # Optional Langfuse tracing via LiteLLM callbacks
├── metrics/
│   └── prometheus.py        # All counter/histogram definitions
└── db/
    ├── models.py             # SQLAlchemy ORM (User, Team, ApiKey, UsageRecord)
    ├── analytics.py          # usage_daily materialized view (Postgres) + hourly refresh
    └── repositories/        # DB access layer

config/config.yaml           # Non-secret configuration
scripts/
├── create_api_key.py        # Provision a user + key from the CLI
└── ingest_kb.py             # Ingest documents into ChromaDB
docker/
├── Dockerfile
├── docker-compose.yml       # proxy + postgres + prometheus + langfuse
└── prometheus.yml
```

---

## Request pipeline

Every chat request passes through these stages in order:

```
1. Auth          — API key → user + team identity
2. Rate limit    — exact token count (litellm.token_counter) checked against RPM + TPM budgets
3. Content policy — regex scan for blocked patterns; token length check
4. PII scrub     — Presidio NLP + custom regex; originals stored in per-request map
5. RAG           — embed last user message → ChromaDB top-K → inject as system prompt prefix
6. Cache check   — litellm.Cache lookup (exact match on scrubbed messages)
7. LLM call      — litellm.acompletion with fallbacks; retries on transient errors
8. PII restore   — placeholders in response replaced with originals
9. Record        — usage written to DB; Prometheus counters updated; Langfuse trace closed
```

---

## Development

```bash
# Run tests
pytest

# Run with auto-reload
uvicorn app.main:app --reload --port 8000

# Disable RAG and PII for faster local iteration
RAG__ENABLED=false PII__ENABLED=false uvicorn app.main:app --reload
```

Tests use SQLite in-memory and skip RAG by default. PII tests require the spaCy model (`python -m spacy download en_core_web_lg`).
