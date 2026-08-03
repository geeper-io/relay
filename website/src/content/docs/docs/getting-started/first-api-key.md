---
title: First API key
description: Create your first API key through Relay's developer portal or the admin API.
---

There are two ways to get an API key: the admin API for operations and automation, or the SSO-backed developer portal
for self-service.

## Option A: Admin API

All admin endpoints require the `PROXY_MASTER_KEY` in the `Authorization` header.

### 1. Create a team (optional)

```bash
curl -X POST \
  'http://localhost:8000/internal/teams?name=engineering&tpm_limit=200000&daily_token_limit=5000000' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

### 2. Create a user

```bash
curl -X POST \
  'http://localhost:8000/internal/users?external_id=alice%40example.com&team_id=<team-uuid>' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

### 3. Issue an API key

```bash
curl -X POST \
  'http://localhost:8000/internal/api-keys?user_id=<user-uuid>&name=dev-laptop&scopes=chat&scopes=responses&scopes=embeddings&scopes=rag%3Arepo%3Amyorg%2Fbackend&expires_at=2026-12-31T23%3A59%3A59Z' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "id": "<key-uuid>",
  "key": "gr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "gr-xxxxxxxxx",
  "scopes": ["chat", "responses", "embeddings", "rag:repo:myorg/backend"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

:::caution
The full `key` is returned **once**. It is stored as a SHA-256 hash — it cannot be retrieved again. Save it immediately.
:::

`chat` is the default when `scopes` is omitted. Add `responses` for `/v1/responses`. RAG is fail-closed: add
`rag:repo:owner/name` or `rag:*` explicitly.

## Option B: Developer portal

With general OIDC or Google compatibility credentials configured, users can sign in, see usage and limits, and manage
their own scoped keys without admin intervention.

### Setup

1. Create an OAuth 2.0 Web Application client in [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Add your proxy URL as an authorised redirect URI: `https://proxy.internal/auth/callback`
3. Set the environment variables:

```bash
GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
AUTH_BASE_URL=https://proxy.internal
```

Helm:
```yaml
secrets:
  googleClientId: "123456789-abc.apps.googleusercontent.com"
  googleClientSecret: "GOCSPX-..."
  authBaseUrl: "https://proxy.internal"
```

### User flow

1. User visits `https://proxy.internal/auth/login`
2. Redirected to Google consent screen
3. On approval, redirected back to `/auth/callback`
4. Proxy verifies the HMAC-signed state parameter, exchanges the code for a Google token
5. User's Google account email is used to upsert the user in the database
6. Relay creates a signed user session and redirects to `/portal`
7. The user creates a named, expiring key and sees the raw secret once

The portal also provides ready-to-copy OpenAI, Anthropic, Claude Code, MCP, and Responses API configurations.

### Key boundaries

Users may select only scopes listed in `oidc.default_key_scopes`. Relay enforces the configured active-key count and
maximum self-service TTL, and users can rotate or revoke only keys belonging to their identity. Rotation invalidates
the previous secret immediately.

:::tip
To share the proxy with a team, send them to `/auth/login`. They each get their own key tied to their Google identity, billed to their user in the usage reports.
:::
