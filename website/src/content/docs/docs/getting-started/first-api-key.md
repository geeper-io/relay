---
title: First API key
description: Create your first API key via the admin API or Google SSO.
---

There are two ways for users to get an API key: the admin API (for ops/automation) and Google SSO (for self-service).

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
  'http://localhost:8000/internal/api-keys?user_id=<user-uuid>&name=dev-laptop&scopes=chat&scopes=embeddings&scopes=rag%3Arepo%3Amyorg%2Fbackend&expires_at=2026-12-31T23%3A59%3A59Z' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "id": "<key-uuid>",
  "key": "gr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "gr-xxxxxxxxx",
  "scopes": ["chat", "embeddings", "rag:repo:myorg/backend"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

:::caution
The full `key` is returned **once**. It is stored as a SHA-256 hash — it cannot be retrieved again. Save it immediately.
:::

`chat` is the default when `scopes` is omitted. RAG is fail-closed: add `rag:repo:owner/name` or `rag:*` explicitly.

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
6. A new chat-scoped API key named `sso` is created and displayed in the browser

The key is shown once in the callback page — users should copy it to their `.env` or shell profile.

### Subsequent logins

Each login creates a new key. Existing keys remain valid until they expire or an administrator revokes them with
`DELETE /internal/api-keys/{key_id}`. Use `GET /internal/api-keys?user_id={user_id}` to find the key ID by its name and
prefix.

:::tip
To share the proxy with a team, send them to `/auth/login`. They each get their own key tied to their Google identity, billed to their user in the usage reports.
:::
