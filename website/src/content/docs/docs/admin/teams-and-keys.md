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

Supported scopes are `chat`, `embeddings`, `rag:repo:owner/name`, `rag:*`, and the global `*`. If no scopes are
provided, the key receives `chat` only. The optional `expires_at` is checked both during database lookup and on cached
identities.

The current API creates keys but does not yet list, revoke, or rotate them. Rotate by issuing a replacement and then
deactivating the old `api_keys` row (`is_active=false`) through your administrative database workflow.

## Security model

- Keys are stored as SHA-256 hashes — a database compromise does not expose usable keys
- Expired and inactive keys are rejected; capability and RAG repository scopes are enforced per request
- The master key (`PROXY_MASTER_KEY`) is the only secret with admin access — rotate it by updating the Kubernetes Secret and restarting pods
- User and team minute/day budgets are enforced together; any user can consume the shared team quota, so size it for
  aggregate team traffic
