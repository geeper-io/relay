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
curl -X POST http://localhost:8000/internal/teams \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "engineering",
    "tpm_limit": 200000,
    "daily_token_limit": 5000000
  }'
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Team display name |
| `tpm_limit` | int | no | Team-wide tokens per minute limit (overrides global default) |
| `daily_token_limit` | int | no | Team-wide tokens per day limit |

### List teams

```bash
curl http://localhost:8000/internal/teams \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Users

### Create a user

```bash
curl -X POST http://localhost:8000/internal/users \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "alice@example.com",
    "team_id": "team_01j..."
  }'
```

| Field | Type | Required | Description |
|---|---|---|---|
| `external_id` | string | yes | Your identifier for the user (email, employee ID, etc.) |
| `team_id` | string | no | Associate user with a team |

### List users

```bash
curl http://localhost:8000/internal/users \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## API keys

### Create a key

```bash
curl -X POST http://localhost:8000/internal/api-keys \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "laptop-dev",
    "user_id": "user_01j..."
  }'
```

Response:

```json
{
  "id": "ak_01j...",
  "name": "laptop-dev",
  "key": "llmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "llmp_xxxx",
  "user_id": "user_01j...",
  "created_at": "2025-01-01T00:00:00Z"
}
```

:::caution
The `key` is shown **once**. Keys are stored as SHA-256 hashes — the original cannot be recovered.
:::

### List keys

```bash
curl http://localhost:8000/internal/api-keys \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Returns key metadata only (prefix, name, user, dates) — never the full key value.

### Rotate a key

There is no dedicated rotation endpoint. To rotate:

1. Create a new key for the same user
2. Distribute the new key to the user
3. Delete the old key once confirmed

### Delete a key

```bash
curl -X DELETE http://localhost:8000/internal/api-keys/ak_01j... \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Security model

- Keys are stored as SHA-256 hashes — a database compromise does not expose usable keys
- The master key (`PROXY_MASTER_KEY`) is the only secret with admin access — rotate it by updating the Kubernetes Secret and restarting pods
- Rate limits are enforced at the user level, aggregated up to team level — a single user cannot exhaust team quota alone (unless they are the only user in the team)
