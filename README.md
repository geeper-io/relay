# Relay

[![License: MIT](https://img.shields.io/badge/License-MIT-teal.svg)](LICENSE)
[![Helm](https://img.shields.io/badge/Helm-3.x-blue?logo=helm)](helm/relay)
[![OpenAI compatible](https://img.shields.io/badge/OpenAI-compatible-412991?logo=openai)](docs/configuration.md)
[![Anthropic compatible](https://img.shields.io/badge/Anthropic-compatible-D4A027)](docs/configuration.md)

Open source AI gateway with RAG, PII scrubbing, and usage controls.
Drop-in OpenAI & Anthropic compatible. Deploy to Kubernetes in minutes.

## Features

- **Multi-provider** — OpenAI, Anthropic, Azure OpenAI,
  and [any LiteLLM-supported provider](https://docs.litellm.ai/docs/providers)
- **OpenAI-compatible** — `/v1/chat/completions` and `/v1/embeddings`; existing tools work without modification
- **Anthropic Messages API** — native `/v1/messages` endpoint so Claude Code and the Anthropic SDK connect without any
  adapter; full tool use and streaming support
- **One key mode** — add your LLM provider key once, then log in and receive a Relay API key to use across all tools
- **Passthrough key mode** — point your SDK or agent at Relay with your own provider key; Relay applies all
  middleware without issuing a separate key
- **PII scrubbing** — strips personal data from requests before they leave your network using Microsoft Presidio (
  NLP-based) and custom regex patterns; restores placeholders in responses
- **RAG / internal knowledge base** — enriches answers with context from your internal docs (Markdown, text) via
  ChromaDB vector search
- **Usage tracking** — every request is logged with model, tokens, cost, latency, and user identity to a database
- **Prometheus metrics** — request count, latency, token usage, cost, cache hits, PII events, RAG hits, and rate limit
  events
- **Rate limiting** — per-user and per-team token/request budgets (in-memory or Redis)
- **Response caching** — exact-match cache via LiteLLM (local or Redis); `X-Cache-Hit: true` header on cache hits
- **Model fallbacks** — automatic failover to backup models on errors or context-window overflow
- **Content policy** — blocks prompt-injection patterns and oversized inputs
- **Langfuse analytics** — optional per-request LLM tracing with user IDs, session grouping, and cost
- **Admin API** — manage users, teams, and API keys; pull usage reports

## Why Relay

|                          | Relay | LiteLLM Proxy | Portkey | OpenRouter |
|--------------------------|:-----:|:-------------:|:-------:|:----------:|
| Self-hosted              |   ✅   |       ✅       |    ❌    |  ❌         |
| OpenAI compatible        |   ✅   |       ✅       |    ✅    |     ✅      |
| Anthropic compatible     |   ✅   |       ✅       |    ✅    |     ✅      |
| RAG / internal knowledge |   ✅   |       ❌       |    ❌    |     ❌      |
| PII scrubbing            |   ✅   |       ❌       |    ❌    |     ❌      |
| SSO                      |   ✅   |       ❌       |    ❌    |     ❌      |
| Per-user rate limiting   |   ✅   |       ✅       |    ✅    |     ❌      |
| Kubernetes Helm chart    |   ✅   |       ✅       |    ❌    |     ❌      |
| MIT license              |   ✅   |       ✅       |    ❌    |     ❌      |

---

## Quick start

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami && helm repo update
helm dependency build helm/relay
helm upgrade --install relay helm/relay \
  --namespace relay --create-namespace \
  --set secrets.openaiApiKey=sk-... \
  --set secrets.anthropicApiKey=sk-ant-... \
  --set postgresql.auth.password=your-db-password
```

The proxy is now available. Point any OpenAI-compatible tool at it — change one line:

```bash
# OpenAI SDK / curl
export OPENAI_BASE_URL=https://relay.internal/v1

# Anthropic SDK
export ANTHROPIC_BASE_URL=https://relay.internal

# Claude Code
export ANTHROPIC_BASE_URL=https://relay.internal
export ANTHROPIC_AUTH_TOKEN=gr-...
```

Your existing code works without any other changes. Get your `gr-...` key by logging in at
`https://relay.internal/auth/login` (Google SSO) or ask your admin to issue one.

---

## Request pipeline

Every request passes through 9 stages in order:

![Request pipeline](docs/pipeline.png)

---

## Kubernetes (Helm)

Requires Helm 3.x and a cluster with a default StorageClass. `PROXY_MASTER_KEY` is auto-generated on first install and
preserved across upgrades.

For production values, scaling, and secret management see [docs/helm.md](docs/helm.md).

---

## Docker

```bash
cp .env.example .env  # fill in keys

# Build and start everything
docker compose -f docker/docker-compose.yml up -d

# Run standalone (SQLite, no Postgres required)
docker build -t relay .
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e PROXY_MASTER_KEY=secret \
  -v $(pwd)/chroma_data:/app/chroma_data \
  -v $(pwd)/knowledge_base:/app/knowledge_base \
  relay
```

Services started by compose: `proxy` (port 8000), `postgres`, `prometheus` (port 9090).

**Worker count** defaults to 4. Override with `-e WORKERS=8` or set `WORKERS=8` in `.env`.

---

## Development

```bash
pip install -r requirements-dev.txt

# Run tests
pytest

# Run with auto-reload
uvicorn app.main:app --reload --port 8000

# Disable RAG and PII for faster local iteration
RAG__ENABLED=false PII__ENABLED=false uvicorn app.main:app --reload
```

Tests use SQLite in-memory and skip RAG by default. PII tests require a spaCy model (
`python -m spacy download en_core_web_sm`).

---

## Further reading

- [Configuration reference](docs/configuration.md) — LLM providers, PII, RAG, rate limiting, caching, content policy,
  Google SSO, Langfuse
- [Helm reference](docs/helm.md) — production values, scaling, secret management
- [Admin API](docs/admin-api.md) — user/key management, usage reports, leaderboards, Prometheus metrics
