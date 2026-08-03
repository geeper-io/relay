---
title: OIDC & Google SSO
description: Self-service developer portal with any OpenID Connect provider or legacy Google configuration.
---

Relay supports provider discovery for Okta, Entra ID, Keycloak, Auth0, Dex, and other OpenID Connect providers. The
existing Google-specific variables remain supported as a compatibility fallback.

## General OIDC

```bash
OIDC__ISSUER_URL=https://id.example.com
OIDC__CLIENT_ID=relay
OIDC__CLIENT_SECRET=...
AUTH_BASE_URL=https://proxy.internal
```

The issuer's `/.well-known/openid-configuration` must match the configured issuer and provide authorization, token,
and userinfo endpoints. Configure optional claim/domain/key-scope controls in YAML:

```yaml
oidc:
  allowed_email_domains: [example.com]
  require_verified_email: true
  default_key_scopes: [chat, responses]
  token_endpoint_auth_method: client_secret_post # or client_secret_basic
```

## Google compatibility configuration

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
     → issues a signed, HttpOnly portal session
     → redirects to /portal
```

## State parameter security

The `state` parameter is a HMAC-SHA256 signed nonce:

```
state = nonce + "." + HMAC-SHA256(secret, nonce)[:16]
```

This is **stateless** — no server-side session storage is required. It works correctly with multiple uvicorn workers and multiple Kubernetes replicas. The `PROXY_MASTER_KEY` is used as the HMAC secret.

## Developer portal

The portal shows each user's effective limits, recent usage, model breakdown, and safe key metadata. Users create,
rotate, and revoke only their own keys. Self-service scopes are capped by `oidc.default_key_scopes`, secrets are shown
once, and key mutations require a session-bound CSRF token.

It also includes copy-ready setup for OpenAI-compatible and Anthropic SDKs, Claude Code, remote MCP clients, and
Responses API workflows. The old one-shot key page is temporarily available at `/auth/login?issue_key=true`.

## Disabling

Login is disabled when neither complete OIDC nor Google credentials are configured. `/auth/login` then returns 501.
Set `portal.enabled: false` to disable the self-service UI independently.

## Restricting to a specific domain

Set `oidc.allowed_email_domains` even when using the Google compatibility credentials:

```yaml
oidc:
  allowed_email_domains: [example.com]
```

Relay rejects identities outside the allowlist before creating a user or portal session.
