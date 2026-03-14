---
title: API overview & authentication
description: Authentication model, request headers, and error shapes for all LLM Proxy endpoints.
---

LLM Proxy exposes three groups of endpoints:

| Group | Path prefix | Auth |
|---|---|---|
| OpenAI-compatible inference | `/v1/chat/completions`, `/v1/models` | API key |
| Anthropic Messages API | `/v1/messages` | API key |
| Admin | `/internal/*` | Master key |
| Health & metrics | `/healthz`, `/readyz`, `/metrics` | None |

## Authentication

### API key (inference endpoints)

Pass the key in the `Authorization` header:

```
Authorization: Bearer llmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Or, for Anthropic-format clients:

```
x-api-key: llmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Keys are issued via the admin API (`POST /internal/api-keys`) or via Google SSO. See [First API key](/docs/getting-started/first-api-key).

### Master key (admin endpoints)

The `PROXY_MASTER_KEY` grants full admin access. Use it only for automation and key provisioning — never distribute it to end users.

```
Authorization: Bearer <PROXY_MASTER_KEY>
```

## Request ID

Every response includes an `x-request-id` header with a UUID. Include this in bug reports and log queries.

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
| `model_not_allowed` | 400 | Model not in `allowedModels` |
| `upstream_error` | 502 | LLM provider returned an error |
| `internal_error` | 500 | Unexpected proxy error |

## Rate limit headers

On a 429 response:

```
Retry-After: 47
```

Value is seconds until the rate-limiting bucket refills enough to allow the request.
