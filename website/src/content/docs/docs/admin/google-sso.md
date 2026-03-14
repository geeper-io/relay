---
title: Google SSO
description: Self-service API key provisioning via Google OAuth 2.0.
---

When Google OAuth credentials are configured, users can visit `/auth/login`, sign in with their Google account, and receive an API key — no admin intervention needed.

## Setup

### 1. Create a Google OAuth client

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Application type: **Web application**
4. Add Authorised redirect URI: `https://proxy.internal/auth/callback`
5. Copy the **Client ID** and **Client secret**

### 2. Configure the proxy

Environment variables:

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

:::caution
`AUTH_BASE_URL` must match the domain in your Authorised redirect URI exactly (no trailing slash).
:::

### 3. Verify

Visit `https://proxy.internal/auth/login`. You should be redirected to Google's consent screen.

## User flow

```
User → GET /auth/login
     → 302 to accounts.google.com/o/oauth2/auth
     → (user signs in and approves)
     → 302 to /auth/callback?code=...&state=...
     → proxy verifies HMAC state, exchanges code for token
     → fetches user profile (name, email) from Google
     → upserts user in database (create on first login, update on subsequent)
     → creates API key named "sso"
     → returns HTML page showing the key
```

## State parameter security

The `state` parameter is a HMAC-SHA256 signed nonce:

```
state = nonce + "." + HMAC-SHA256(secret, nonce)[:16]
```

This is **stateless** — no server-side session storage is required. It works correctly with multiple uvicorn workers and multiple Kubernetes replicas. The `PROXY_MASTER_KEY` is used as the HMAC secret.

## Key management

- Each login creates a **new** key named `sso` — it does not replace the previous one
- Old keys remain valid unless deleted via the admin API
- The key is displayed once in the callback HTML page — users should save it immediately

## Disabling

OAuth is disabled automatically when `GOOGLE_CLIENT_ID` is empty. The `/auth/login` endpoint returns 404.

## Restricting to a specific domain

The current implementation does not restrict logins by email domain — any Google account can obtain a key. To restrict access:

1. Set `GOOGLE_CLIENT_ID` only internally and do not publicly advertise `/auth/login`
2. Or add domain validation to `app/api/auth.py` after fetching the user profile:

```python
if not userinfo["email"].endswith("@yourcompany.com"):
    raise HTTPException(status_code=403, detail="Unauthorized domain")
```
