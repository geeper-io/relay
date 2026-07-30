---
title: API overview & authentication
description: Authentication model, request headers, and error shapes for all Geeper Relay endpoints.
---

Geeper Relay exposes these endpoint groups:

| Group | Path prefix | Auth |
|---|---|---|
| OpenAI-compatible inference | `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models` | API key |
| Anthropic Messages API | `/v1/messages` | API key |
| Admin | `/internal/*` | Master key |
| Health | `/healthz`, `/readyz` | None |
| Metrics | `/metrics` | Master key by default |

## Authentication

Relay-issued keys are the secure default. Optional passthrough/BYOK can be enabled explicitly.

### Relay-issued keys

Keys issued by Relay start with `gr-`. Pass them in the `Authorization` header:

```
Authorization: Bearer gr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Keys are issued via the admin API (`POST /internal/api-keys`) or via Google SSO. See [First API key](/docs/getting-started/first-api-key).
Administrators can inventory, revoke, and atomically rotate keys through the same API; raw secrets are only returned
at creation or rotation time.

Keys can expire and carry capability/data scopes:

| Scope | Access |
|---|---|
| `chat` | `/v1/chat/completions`, `/v1/messages`, `/v1/models` |
| `responses` | `/v1/responses` |
| `embeddings` | `/v1/embeddings` |
| `rag:repo:owner/name` | RAG retrieval from one repository |
| `rag:*` | RAG retrieval from all repositories |
| `*` | All API capabilities |

### Passthrough keys (bring your own)

Passthrough is disabled by default. When `server.allow_passthrough_keys` is explicitly set to `true`, a key that does
**not** start with `gr-` is forwarded to the upstream provider. It receives `chat`, `responses`, and `embeddings` capability but no
RAG scopes, and it is not written to Relay's user-attributed usage/audit tables.

This lets employees point their existing SDK at Relay without being issued a separate key:

```bash
export ANTHROPIC_BASE_URL=https://relay.company.com
# ANTHROPIC_API_KEY stays as their own key — no changes needed
```

Works with any provider — Anthropic, OpenAI, Azure, Gemini, etc. The upstream provider authenticates the key; Relay never validates it.

Only enable this for trusted BYOK deployments where bypassing Relay identity and persistent accounting is acceptable.

### Master key (admin endpoints)

The `PROXY_MASTER_KEY` grants full admin access and protects `/metrics` by default. Use it only for automation,
monitoring, and key provisioning—never distribute it to end users. Startup rejects missing and known placeholder keys.

```
Authorization: Bearer <PROXY_MASTER_KEY>
```

## Request ID

Every response includes an `x-request-id` header with a UUID. Include this in bug reports and log queries.

Inference responses also include `X-Relay-Deployment` and `X-Relay-Policy-Version` so callers can correlate behavior
with a concrete versioned routing decision.

## Error envelope

All error responses use a consistent JSON shape:

```json
{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Token rate limit exceeded. Retry after 47 seconds.",
    "code": 429
  }
}
```

Common error types:

| `type` | HTTP status | Description |
|---|---|---|
| `authentication_error` | 401 | Invalid or missing API key |
| `content_policy_violation` | 400 | Blocked pattern or token limit exceeded |
| `rate_limit_exceeded` | 429 | Token-bucket limit hit |
| `model_not_allowed` | 400 | Model/deployment disallowed or missing a required capability |
| `upstream_error` | 502 | LLM provider returned an error |
| `internal_error` | 500 | Unexpected proxy error |

## Rate limit headers

On a 429 response:

```
Retry-After: 47
```

Value is seconds until the rate-limiting bucket refills enough to allow the request.
