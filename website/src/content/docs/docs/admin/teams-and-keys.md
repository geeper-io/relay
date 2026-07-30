---
title: Teams & API keys
description: Manage teams, users, and API keys via the admin API.
---

All admin endpoints require the master key:

```
Authorization: Bearer <PROXY_MASTER_KEY>
```

## Teams

### Create a team

```bash
curl -X POST \
  'http://localhost:8000/internal/teams?name=engineering&tpm_limit=200000&daily_token_limit=5000000' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Team display name |
| `tpm_limit` | int | no | Team-wide tokens per minute limit (overrides global default) |
| `daily_token_limit` | int | no | Team-wide tokens per day limit |

## Users

### Create a user

```bash
curl -X POST \
  'http://localhost:8000/internal/users?external_id=alice%40example.com&team_id=<team-uuid>' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

| Field | Type | Required | Description |
|---|---|---|---|
| `external_id` | string | yes | Your identifier for the user (email, employee ID, etc.) |
| `team_id` | string | no | Associate user with a team |

### Look up a user

```bash
curl 'http://localhost:8000/internal/users?external_id=alice%40example.com' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## API keys

### Create a key

```bash
curl -X POST \
  'http://localhost:8000/internal/api-keys?user_id=<user-uuid>&name=laptop-dev&scopes=chat&scopes=rag%3Arepo%3Amyorg%2Fbackend&expires_at=2026-12-31T23%3A59%3A59Z' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "id": "<key-uuid>",
  "key": "gr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "gr-xxxxxxxxx",
  "scopes": ["chat", "rag:repo:myorg/backend"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

:::caution
The `key` is shown **once**. Keys are stored as SHA-256 hashes — the original cannot be recovered.
:::

Supported scopes include `chat`, `responses`, `embeddings`, `rag:repo:owner/name`, `rag:*`, granular MCP scopes
(`mcp:*`, `mcp:server:*`, or `mcp:server:tool`), and the global `*`. If no scopes are
provided, the key receives `chat` only. The optional `expires_at` is checked both during database lookup and on cached
identities.

### List keys

Key inventory responses contain metadata only. Raw keys and key hashes are never returned.

```bash
curl 'http://localhost:8000/internal/api-keys?user_id=<user-uuid>&include_inactive=true' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Use `limit` (1–500) and `offset` for pagination. By default, revoked keys are omitted; expired keys remain visible with
`status: "expired"` because their database row is still active.

### Revoke a key

```bash
curl -X DELETE 'http://localhost:8000/internal/api-keys/<key-uuid>' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Revocation is idempotent and takes effect on the next request across all workers and replicas. Relay-issued identities
are resolved against the shared database rather than held in a positive in-process cache.

### Rotate a key

```bash
curl -X POST 'http://localhost:8000/internal/api-keys/<key-uuid>/rotate' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Rotation atomically revokes the old key and creates a replacement with the same owner, name, scopes, and expiry. The
new raw `key` is returned once. To set a new expiry instead, pass
`preserve_expiry=false&expires_at=2027-12-31T23:59:59Z`.

Key creation, revocation, and rotation write audit events without storing raw secrets.

## Security model

- Keys are stored as SHA-256 hashes — a database compromise does not expose usable keys
- Expired and inactive keys are rejected; capability and RAG repository scopes are enforced per request
- Key rotation and revocation are transactional; revoked keys are checked against the shared database on every request
- The master key (`PROXY_MASTER_KEY`) is the only secret with admin access — rotate it by updating the Kubernetes Secret and restarting pods
- User and team minute/day budgets are enforced together; any user can consume the shared team quota, so size it for
  aggregate team traffic
