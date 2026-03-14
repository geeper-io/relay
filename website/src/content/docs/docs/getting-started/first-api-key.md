---
title: First API key
description: Create your first API key via the admin API or Google SSO.
---

There are two ways for users to get an API key: the admin API (for ops/automation) and Google SSO (for self-service).

## Option A: Admin API

All admin endpoints require the `PROXY_MASTER_KEY` in the `Authorization` header.

### 1. Create a team (optional)

```bash
curl -X POST http://localhost:8000/internal/teams \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "engineering",
    "tpm_limit": 200000,
    "daily_token_limit": 5000000
  }'
```

### 2. Create a user

```bash
curl -X POST http://localhost:8000/internal/users \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "alice@example.com",
    "team_id": "team_01j..."
  }'
```

### 3. Issue an API key

```bash
curl -X POST http://localhost:8000/internal/api-keys \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dev-laptop",
    "user_id": "user_01j..."
  }'
```

Response:

```json
{
  "id": "ak_01j...",
  "name": "dev-laptop",
  "key": "llmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "llmp_xxxx",
  "user_id": "user_01j...",
  "created_at": "2025-01-01T00:00:00Z"
}
```

:::caution
The full `key` is returned **once**. It is stored as a SHA-256 hash — it cannot be retrieved again. Save it immediately.
:::

### List existing keys

```bash
curl http://localhost:8000/internal/api-keys \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Returns key metadata (prefix, name, user, created date) — never the full key.

## Option B: Google SSO

When `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are configured, users can obtain their own key by signing in with Google — no admin intervention needed.

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
6. A new API key named `sso` is created and displayed in the browser

The key is shown once in the callback page — users should copy it to their `.env` or shell profile.

### Subsequent logins

Each login creates a new key. Old keys remain valid unless deleted. Users can see their key prefix in the callback page to identify which key is current.

:::tip
To share the proxy with a team, send them to `/auth/login`. They each get their own key tied to their Google identity, billed to their user in the usage reports.
:::
