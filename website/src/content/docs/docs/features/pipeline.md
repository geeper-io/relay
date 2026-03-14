---
title: Request pipeline
description: The 9-stage pipeline every inference request passes through.
---

Every request to `/v1/chat/completions` or `/v1/messages` passes through nine stages in order. Each stage can independently reject, transform, or short-circuit the request.

## Stage overview

```
Client request
      │
      ▼
┌─────────────────────────┐
│  01  Authentication     │  Identify user, resolve team
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  02  Content Policy     │  Block patterns, check token count
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  03  Token Count        │  Count prompt tokens, enforce model limit
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  04  Rate Limiting      │  req/min, tokens/min, tokens/day
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  05  PII Scrubbing      │  Detect & replace sensitive entities
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  06  RAG Context        │  Semantic search, inject chunks
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  07  Cache Lookup       │  Return cached response if hit
└─────────────┬───────────┘
              │ (miss)
              ▼
┌─────────────────────────┐
│  08  LLM Call           │  Route via LiteLLM, fallback models
└─────────────┬───────────┘
              │
              ▼
┌─────────────────────────┐
│  09  Metrics & Usage    │  Record to DB, restore PII, emit metrics
└─────────────────────────┘
              │
              ▼
        Client response
```

## Stage details

### 01 — Authentication

- Extracts API key from `Authorization: Bearer` or `x-api-key` header
- Looks up key hash in the database (SHA-256 comparison)
- Resolves associated user and team
- Attaches user/team context to the request for downstream stages
- **Rejects with 401** if key is missing, unknown, or revoked

### 02 — Content Policy

- Checks the concatenated prompt text against `content_policy.blocked_patterns` (case-insensitive literal match)
- **Rejects with 400** (`content_policy_violation`) if any pattern matches
- Runs **before** token counting to fail fast on obvious attacks
- Disabled by setting `content_policy.enabled: false`

### 03 — Token Count

- Counts prompt tokens using `tiktoken` (model-appropriate encoding)
- Enforces `content_policy.max_input_tokens` (default 32 000)
- Stores the count for stage 04 (rate limiting deducts from buckets)
- **Rejects with 400** if the prompt exceeds the token limit

### 04 — Rate Limiting

Three token buckets checked in order, any can reject:

1. **User req/min** — `rate_limiting.defaults.requests_per_minute`
2. **User tokens/min** — `rate_limiting.defaults.tokens_per_minute`
3. **User tokens/day** — `rate_limiting.defaults.tokens_per_day`
4. **Team tokens/min** — team's `tpm_limit` (if team has override)

**Rejects with 429** and `Retry-After` header on any overflow.

See [Rate limiting](/docs/features/rate-limiting) for bucket mechanics and Redis backend.

### 05 — PII Scrubbing

- Runs Presidio `AnalyzerEngine` across all message content
- Detected entities are replaced with deterministic placeholders: `<<PII_EMAIL_ADDRESS_a3f8c1d0>>`
- The placeholder→original mapping is stored in request context for stage 09
- Disabled by setting `pii.enabled: false`

See [PII scrubbing](/docs/features/pii-scrubbing).

### 06 — RAG Context

- Embeds the last user message with `all-MiniLM-L6-v2`
- Queries ChromaDB for top-k chunks above `score_threshold`
- Injects retrieved chunks as a prefix in the system message
- No-op if ChromaDB is empty or if `rag.enabled: false`

See [RAG integration](/docs/features/rag).

### 07 — Cache Lookup

- Hashes the (normalized messages + model) to a cache key
- Returns the cached response immediately on hit — stages 08–09 are skipped
- Disabled by setting `cache.enabled: false` (default)

### 08 — LLM Call

- Routes to the provider via LiteLLM based on the model name prefix
- On provider error (5xx, timeout): tries `fallback_models` in order
- Supports streaming (SSE) pass-through for both OpenAI and Anthropic formats

### 09 — Metrics & Usage

- Counts completion tokens from the response
- Writes a `UsageRecord` to PostgreSQL (user, team, model, prompt tokens, completion tokens, cost, latency)
- Restores PII placeholders in the response content (reverse of stage 05)
- Increments Prometheus counters
- Stores response in cache if `cache.enabled: true` (non-streaming only)

## Skipping stages

Each non-authentication stage can be disabled in `config.yaml`:

```yaml
rag:
  enabled: false
pii:
  enabled: false
content_policy:
  enabled: false
rate_limiting:
  enabled: false
cache:
  enabled: false
```

Authentication and metrics recording cannot be disabled.
