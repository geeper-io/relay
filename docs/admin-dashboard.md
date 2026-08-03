# Admin dashboard

Relay includes an opt-in browser operations console at `/admin`. It is served by Relay, so usage data, user metadata,
approval data, and decisions remain same-origin with the control-plane API.

The console currently provides:

- an operational overview of requests, tokens, estimated provider cost, errors, latency, cache hits, users, and teams;
- daily request volume plus the highest-cost users and models for a selected 7-, 30-, or 90-day window;
- a searchable user directory with effective limits, team, account status, usage, cost, activity, and key counts;
- user detail with per-model usage and API-key metadata, without raw keys or hashes;
- pending, approved, denied, consumed, and expired requests;
- requester, team, server, tool, purpose, policy version, and exact arguments;
- approve-once and deny actions with an audit reason;
- active, expired, exhausted, and revoked user/team approval grants with usage and expiry;
- admin-only creation and immediate revocation of scoped standing grants;
- live MCP server health, discovery latency, tool inventory, and published input schemas;
- immutable MCP policy drafts with validation, diffs, simulation, activation history, and one-click rollback;
- expiry warnings and automatic queue refresh;
- responsive keyboard-accessible request details.

The interface uses the MIT-licensed [Tabler](https://github.com/tabler/tabler) design system, pinned to version 1.4.0.

## Local demo

From the repository root, run:

```bash
./scripts/run_admin_demo.sh
```

Open `http://127.0.0.1:8000/admin` and use the displayed demo master key. The command creates an isolated temporary
SQLite database with realistic synthetic teams, users, limits, API-key metadata, 45 days of usage, admin identities,
MCP approval requests, grant offers, grants in multiple lifecycle states, and active, archived, and draft MCP policy
versions. It disables integrations that are unnecessary for the UI demo and removes the temporary
database when the process stops.

Useful options:

```bash
./scripts/run_admin_demo.sh --port 8080 --seed 7
./scripts/run_admin_demo.sh --database ./relay-demo.db
./scripts/run_admin_demo.sh --seed-only --database ./relay-demo.db
```

The default key and dataset are for local demonstration only. Do not expose this process outside localhost or reuse the
demo master key in any deployed environment.

## Enable it

```yaml
admin:
  enabled: true
  session_ttl_seconds: 28800
  secure_cookies: true
  oidc_enabled: true
  bootstrap_emails:
    - relay-admin@example.com
  allow_master_key_login: true
```

Or with environment variables:

```env
ADMIN__ENABLED=true
ADMIN__SESSION_TTL_SECONDS=28800
ADMIN__SECURE_COOKIES=true
ADMIN__OIDC_ENABLED=true
ADMIN__BOOTSTRAP_EMAILS=["relay-admin@example.com"]
ADMIN__ALLOW_MASTER_KEY_LOGIN=true
```

Configure Relay's general [OIDC settings](configuration.md#openid-connect), then open
`https://relay.example.com/admin` and select **Continue with company SSO**. The first bootstrap email receives the
`admin` role. Use that account to establish durable role assignments through the internal API, then remove the
bootstrap list from configuration.

The master-key form is an optional break-glass path. Relay compares the key server-side and exchanges it for a signed,
short-lived HttpOnly admin cookie; the key is never kept in browser storage or JavaScript. Set
`allow_master_key_login: false` after validating SSO if your operating model does not permit browser use of the master
key. The master key still protects `/internal` automation APIs.

Keep `secure_cookies: true` in production and expose the dashboard only over HTTPS. For local HTTP development, set it
to `false` explicitly.

## Roles

| Role | Read approvals, grants, servers, policies | Simulate policy | Approve or deny | Create grants and policy drafts; activate/rollback | Manage roles |
| --- | --- | --- | --- | --- | --- |
| `viewer` | Yes | Yes | No | No | No |
| `approver` | Yes | Yes | Yes | No | No |
| `admin` | Yes | Yes | Yes | Yes | Yes, through the dashboard API |

OIDC authenticates the person; Relay's `admin_role_assignments` table authorizes dashboard actions. A database
assignment overrides the bootstrap email list. Role removal or changes are enforced on the next request and require
the user to sign in again.

When an eligible OIDC user attempts dashboard sign-in, Relay records their verified email, display name, last-seen time,
and Relay user ID in the admin identity directory—even if they do not have a role yet. Admins can query
`/admin/api/admin-identities`, then use the session- and CSRF-protected `/admin/api/admin-roles` endpoints. The
master-key `/internal/admin-identities` and `/internal/admin-roles` equivalents remain available for bootstrap,
automation, and recovery:

```bash
# Assign or change a role
curl -X PUT "https://relay.example.com/internal/admin-roles/<user-id>" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role":"approver"}'

# List assignments
curl "https://relay.example.com/internal/admin-roles" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"

# Discover OIDC identities and their current roles
curl "https://relay.example.com/internal/admin-identities" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"

# Revoke dashboard access
curl -X DELETE "https://relay.example.com/internal/admin-roles/<user-id>" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Assignment, change, and removal operations write durable audit events.

## Standing approval grants

The **Approval grants** view lets operators inspect the current scope, tool pattern, policy version, call budget,
expiry, provenance, and lifecycle state. Admins can grant a user or team repeated access to one registered MCP server
and a glob-style tool pattern. Optional argument constraints further narrow the policy rule; they never widen it.

An approval policy may also attach a `grant` offer to a `require_approval` rule. The approval drawer displays that
offer before the decision. Approving creates both the one-time decision and the bounded standing grant atomically;
denying creates no grant. Grants match only the active policy version and remain subject to the caller's current API-key
scope and all current policy constraints.

Relay reserves a call from the grant before contacting the remote MCP server, so failures consume budget. Revoke takes
effect on the next call. Expiry, exhaustion, revocation, creation, and consumption are visible in the inventory and
durable audit log.

## MCP policy control plane

The **MCP policies** view probes every configured remote server and shows its current health, discovery latency, tools,
and input schemas. A failed server remains isolated from the others and its configured credentials are never returned
to the browser.

Relay treats configured policy YAML as the bootstrap version. Admins can save a validated JSON document as a new,
immutable database draft, compare it with the active version, and simulate an identity, scopes, server, tool, and exact
arguments through the same evaluator used by live calls. Validation errors block saving or activation; warnings flag
risky but intentional configurations such as a default `allow`.

Activation atomically changes a singleton database pointer shared by all replicas. The previous version is retained,
and rollback reactivates that immutable version with a required audit reason. Activation history records the actor,
reason, prior version, target version, and timestamp. Existing approval tokens and standing grants remain bound to
their original policy version, so they become stale instead of silently carrying authority into a new policy.

## Decision security

- Dashboard sessions are signed with `PROXY_MASTER_KEY`, expire after the configured TTL, and use `SameSite=Strict`.
- OIDC sessions carry an identity and role, which Relay revalidates against current authorization state on each request.
- Mutation requests require a session-bound CSRF token.
- Dashboard pages disable caching, framing, referrers, and unexpected browser content through response headers.
- Approval decisions reuse Relay's durable, row-locked approval lifecycle and remain single-use.
- Standing grants are bounded by subject, exact server, tool pattern, active policy version, expiry, call count, and
  optional argument constraints; deny decisions always win.
- Every decision records the OIDC user ID (or `master-key` for break-glass sessions) along with the required reason.
- The dashboard never receives delegated MCP credentials or remote MCP server credentials.
- Read-only operational endpoints use the same role-checked session and never expose `PROXY_MASTER_KEY`.
- API-key inventory returns prefixes, lifecycle state, scopes, and timestamps; it never returns raw keys or key hashes.

## Helm

```yaml
config:
  admin:
    enabled: true
    sessionTtlSeconds: 28800
    secureCookies: true
    oidcEnabled: true
    bootstrapEmails: [relay-admin@example.com]
    allowMasterKeyLogin: true
```

No separate service or ingress is needed. Route `/admin` and `/admin/assets` to the existing Relay service.

## Operational notes

- Overview data refreshes every 30 seconds while visible. User data loads on demand and supports server-side search.
- The approval list refreshes every 30 seconds while visible; it does not require WebSockets or SSE.
- The current list is capped at 200 recent records. The internal API remains available for larger operational queries.
- Tabler's stylesheet is loaded from a version-pinned jsDelivr URL under a restrictive content security policy. For
  fully air-gapped deployments, vendor that stylesheet into the Relay image before enabling the dashboard.
- Disabling `admin.enabled` makes dashboard routes return `404`; the existing `/internal` APIs are unaffected.
