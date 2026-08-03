# Developer portal

Relay's SSO-backed portal at `/portal` gives each user a private view of their own capacity and credentials. It shows
requests, tokens, estimated cost, daily usage, model breakdown, effective user/team limits, and API-key metadata.

Users can create named, expiring keys, rotate active keys, and revoke keys they own. Raw secrets are returned once and
never stored. The selectable scope set is capped by `oidc.default_key_scopes`; the portal cannot grant broader access.
Relay also enforces `portal.max_active_keys` and `portal.max_key_ttl_days`.

The **Connect** view provides copy-ready examples for:

- the OpenAI Python SDK through Relay's OpenAI-compatible `/v1` API;
- the Anthropic Python SDK and Claude Code through Relay's Messages API;
- Relay's remote Streamable HTTP MCP endpoint at `/mcp`;
- Responses API requests that expose selected Relay-managed MCP servers.

## Configuration

```yaml
portal:
  enabled: true
  session_ttl_seconds: 28800
  secure_cookies: true
  max_active_keys: 10
  max_key_ttl_days: 365

oidc:
  issuer_url: https://id.example.com
  client_id: relay
  client_secret: $OIDC_CLIENT_SECRET
  allowed_email_domains: [example.com]
  default_key_scopes: [chat, responses, mcp:code:*]
```

Open `/auth/login` to start the portal flow. The callback creates or resolves the Relay user, issues a signed HttpOnly
session, and redirects to `/portal`; it does not create a key merely because the user signed in. The former one-shot
flow is available temporarily at `/auth/login?issue_key=true`.

Portal sessions use a distinct cookie and token type from admin sessions. Relay revalidates the user on each request,
requires a session-bound CSRF token for key mutations, checks ownership server-side, and writes the existing durable
key lifecycle audit events with the portal user as actor.

For local HTTP development only, set `portal.secure_cookies: false`. Keep it enabled behind production HTTPS.
