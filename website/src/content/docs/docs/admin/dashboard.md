---
title: Admin dashboard
description: Review and decide MCP tool approvals in Relay's secure browser inbox.
---

Relay includes an opt-in operations dashboard at `/admin`. It runs on the Relay origin and combines enterprise usage,
user posture, and the human part of MCP governance.

## Try the local demo

From the repository root, run `./scripts/run_admin_demo.sh`, open `http://127.0.0.1:8000/admin`, and use the demo key shown
in the terminal. Relay creates an isolated synthetic dataset with users, teams, limits, usage, key metadata, roles, and
MCP approvals, grant offers, approval grants in active and inactive states, and active, archived, and draft policy
versions. The temporary database is removed when
the process stops.

## Enable the dashboard

```yaml
admin:
  enabled: true
  session_ttl_seconds: 28800
  secure_cookies: true
  oidc_enabled: true
  bootstrap_emails: [relay-admin@example.com]
  allow_master_key_login: true
```

Configure Relay OIDC, open `https://relay.example.com/admin`, and continue with company SSO. An email in
`bootstrap_emails` receives initial `admin` access; durable database assignments support `viewer`, `approver`, and
`admin` roles. Role changes take effect on the next dashboard request.

The master-key form is an optional break-glass path. Relay exchanges the key server-side for a signed, short-lived
HttpOnly session; the key is not stored in browser JavaScript or local storage. Set `allow_master_key_login: false`
after SSO is established if browser master-key access is not allowed.

The overview shows requests, tokens, estimated provider cost, error rate, latency, cache hits, active users, daily
traffic, and top users and models. The searchable user directory shows effective limits, team, status, usage, spend,
activity, and API-key counts. User detail includes model usage and safe key metadata without raw keys or hashes.

The approval inbox provides status filters, expiry warnings, exact JSON arguments, requester and policy context,
mandatory decision reasons, and automatic refresh. Approvals still use Relay's durable single-use lifecycle.

The approval-grants view shows user/team scope, server and tool pattern, policy version, call consumption, expiry,
status, reason, and provenance. Viewers and approvers can inspect grants. Admins can create constrained, expiring,
call-limited grants and revoke active grants immediately. When a policy approval includes a standing-access offer, the
approval drawer explains its scope before the approver decides.

The MCP policies view adds live remote-server health, discovery latency, tool and input-schema inventory, policy
validation, active-version diffs, and simulation with an exact identity, scope set, and argument payload. Admins save
new documents as immutable drafts and activate them with an audit reason. Rollback is the same safe operation applied
to an earlier version; Relay retains the complete activation history. Existing approvals and grants become stale when
their policy version is no longer active.

Keep secure cookies enabled and use HTTPS in production. Set `secure_cookies: false` only for local HTTP development.

Admins can discover verified OIDC users through `/admin/api/admin-identities` and manage durable assignments through
`PUT`, `GET`, and `DELETE /admin/api/admin-roles`. Equivalent master-key-protected `/internal` endpoints remain
available for bootstrap and recovery.
