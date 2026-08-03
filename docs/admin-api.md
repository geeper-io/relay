# Admin API

All admin endpoints require `Authorization: Bearer <PROXY_MASTER_KEY>`.

For interactive MCP review, the opt-in [admin dashboard](admin-dashboard.md) provides a protected approval inbox over
the same durable backend lifecycle.

The dashboard also exposes read-only, cookie-authenticated browser endpoints:

- `GET /admin/api/overview?days=7` for aggregate usage, daily traffic, rankings, and control-plane counts;
- `GET /admin/api/users?q=&days=30&limit=100&offset=0` for the searchable user inventory;
- `GET /admin/api/users/{user_id}?days=30` for limits, usage, model breakdown, and safe API-key metadata.
- `GET /admin/api/mcp/grants?include_inactive=true&limit=200` for standing approval-grant inventory.
- `GET /admin/api/mcp/servers` for live remote-server health, latency, tools, and input schemas;
- `GET /admin/api/mcp/policies` and `POST /admin/api/mcp/policies/simulate` for policy inventory and dry runs.

These endpoints accept dashboard sessions, not the master key. Raw API keys and hashes are never returned.

## MCP approval grants

The master-key API can create, list, and revoke bounded grants for automation and break-glass operations:

```bash
curl -X POST "https://relay.example.com/internal/mcp/grants" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_type":"team",
    "subject_id":"<team-id>",
    "server":"code",
    "tool":"test_*",
    "constraints":{"allowed_values":{"runtime":["python"]}},
    "ttl_seconds":28800,
    "max_calls":50,
    "workflow_id":"release-184",
    "reason":"Reviewed release validation workflow"
  }'

curl "https://relay.example.com/internal/mcp/grants?include_inactive=true" \
  -H "Authorization: Bearer $MASTER_KEY"

curl -X DELETE "https://relay.example.com/internal/mcp/grants/<grant-id>" \
  -H "Authorization: Bearer $MASTER_KEY"
```

Dashboard admins have equivalent CSRF-protected `POST` and `DELETE /admin/api/mcp/grants` operations. Viewers and
approvers can read inventory, but only admins can create or revoke arbitrary grants. The subject and server must
already exist. A grant is valid only for its recorded policy version; activating a new version stops it from matching.

For the MCP approval queue and its role in Responses API pause/resume workflows, see
[Responses API with Relay MCP](mcp-responses.md#decide-the-relay-approval).

## MCP policy control plane

The master-key API supports policy validation, immutable draft creation, simulation, and audited activation or
rollback. Policy documents use the same `default_action` and `rules` shape as `mcp.policies` in configuration.

```bash
# Validate without saving
curl -X POST "https://relay.example.com/internal/mcp/policies/validate" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"document":{"default_action":"deny","rules":[]}}'

# Save an immutable draft
curl -X POST "https://relay.example.com/internal/mcp/policies/drafts" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"version":"2026-08-01","base_version":"2026-07-30","reason":"Restrict production tools","document":{"default_action":"deny","rules":[]}}'

# Simulate the draft with exact caller context and arguments
curl -X POST "https://relay.example.com/internal/mcp/policies/simulate" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"version":"2026-08-01","user_id":"<user-id>","team_id":"<team-id>","scopes":["mcp:code:execute"],"server":"code","tool":"execute","arguments":{"runtime":"python"}}'

# Activate, or roll back by activating an earlier immutable version
curl -X POST "https://relay.example.com/internal/mcp/policies/2026-08-01/activate" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Approved in change request CR-184"}'
```

`GET /internal/mcp/policies` returns the active document, all configured and database versions, diffs from active, and
the append-only activation history. Dashboard sessions have equivalent `/admin/api/mcp/policies...` routes; all roles
may read, validate, and simulate, while only admins may create or activate versions. Every mutation requires an audit
reason. Policy activation also makes approvals and standing grants from earlier versions stale.

## Dashboard role management

OIDC dashboard access uses durable `viewer`, `approver`, and `admin` assignments. These endpoints remain protected by
the master key so role administration is separate from interactive dashboard sessions:

```bash
curl -X PUT "http://localhost:8000/internal/admin-roles/<user-id>" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role":"approver"}'

curl "http://localhost:8000/internal/admin-roles?role=approver" \
  -H "Authorization: Bearer $MASTER_KEY"

curl "http://localhost:8000/internal/admin-identities" \
  -H "Authorization: Bearer $MASTER_KEY"

curl -X DELETE "http://localhost:8000/internal/admin-roles/<user-id>" \
  -H "Authorization: Bearer $MASTER_KEY"
```

The identity directory contains verified OIDC email, display name, last-seen time, Relay user ID, and current role.
Role changes are audited and enforced on the user's next dashboard request.

## User and key management

```bash
# Create a team
curl -X POST "http://localhost:8000/internal/teams?name=engineering" \
  -H "Authorization: Bearer $MASTER_KEY"

# Create a user
curl -X POST "http://localhost:8000/internal/users?external_id=bob@company.com&team_id=<team-id>" \
  -H "Authorization: Bearer $MASTER_KEY"

# Issue a scoped API key with an expiry and access to one RAG repository
curl -X POST "http://localhost:8000/internal/api-keys?user_id=<user-id>&name=laptop&scopes=chat&scopes=rag%3Arepo%3Amyorg%2Fbackend&expires_at=2026-12-31T23%3A59%3A59Z" \
  -H "Authorization: Bearer $MASTER_KEY"
# Returns: { "key": "gr-...", "key_prefix": "gr-XXXXXX", "id": "..." }
# The raw key is shown once and not stored.

# Inventory key metadata (raw keys and hashes are never returned)
curl "http://localhost:8000/internal/api-keys?user_id=<user-id>&include_inactive=true" \
  -H "Authorization: Bearer $MASTER_KEY"

# Revoke a key; repeated calls are safe
curl -X DELETE "http://localhost:8000/internal/api-keys/<key-id>" \
  -H "Authorization: Bearer $MASTER_KEY"

# Atomically revoke a key and return a one-time replacement with the same policy
curl -X POST "http://localhost:8000/internal/api-keys/<key-id>/rotate" \
  -H "Authorization: Bearer $MASTER_KEY"
```

List requests support `user_id`, `include_inactive`, `limit` (1–500), and `offset`. Rotation preserves the previous
expiry by default; use `preserve_expiry=false&expires_at=<ISO-8601>` to replace it. Creation, revocation, and rotation
write audit events, and revocation takes effect through the shared database on the next request across all replicas.

## Usage reports and leaderboards

```bash
# Totals by model (default)
curl "http://localhost:8000/internal/usage" \
  -H "Authorization: Bearer $MASTER_KEY"

# Daily cost per team for the last 30 days
curl "http://localhost:8000/internal/usage?granularity=day&group_by=team&since=2026-02-10" \
  -H "Authorization: Bearer $MASTER_KEY"

# Monthly token burn by model
curl "http://localhost:8000/internal/usage?granularity=month&group_by=model" \
  -H "Authorization: Bearer $MASTER_KEY"

# This month, per user
curl "http://localhost:8000/internal/usage?granularity=day&group_by=user&since=2026-03-01" \
  -H "Authorization: Bearer $MASTER_KEY"
```

The `granularity` parameter (`day` | `week` | `month` | `year`) turns the response into a time series ordered by period.
Without it you get flat totals.

Each row includes: `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`, `requests`, `cache_hits`, `errors`,
`avg_latency_ms`.

**Leaderboards** — top-N entities ranked by a metric:

```bash
# Top 10 users by cost this month
curl "http://localhost:8000/internal/usage/leaderboard?dimension=user&metric=cost_usd&since=2026-03-01" \
  -H "Authorization: Bearer $MASTER_KEY"

# Top 5 teams by token usage
curl "http://localhost:8000/internal/usage/leaderboard?dimension=team&metric=total_tokens&limit=5" \
  -H "Authorization: Bearer $MASTER_KEY"

# Most requested models this week
curl "http://localhost:8000/internal/usage/leaderboard?dimension=model&metric=requests&since=2026-03-10" \
  -H "Authorization: Bearer $MASTER_KEY"
```

Parameters: `dimension` (`user` | `team` | `model`), `metric` (`cost_usd` | `total_tokens` | `requests`), `since`,
`until`, `limit` (default 10).

```json
{
  "dimension": "user",
  "metric": "cost_usd",
  "rows": [
    {
      "rank": 1,
      "user": "alice-uuid",
      "value": 12.34,
      "requests": 345,
      "cost_usd": 12.34,
      "total_tokens": 980000
    },
    {
      "rank": 2,
      "user": "bob-uuid",
      "value": 8.10,
      "requests": 210,
      "cost_usd": 8.10,
      "total_tokens": 620000
    }
  ]
}
```

**PostgreSQL only — materialized view**

On PostgreSQL, a `usage_daily` materialized view is created at startup and refreshed every hour in the background. It
pre-aggregates all records by `(day, user, team, model)`, so leaderboard and time-series queries stay fast regardless of
how many raw rows accumulate. On SQLite (dev) all queries run directly against `usage_records`.

## Knowledge base management

All KB endpoints require `Authorization: Bearer <PROXY_MASTER_KEY>`.

**Upload a file:**

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $MASTER_KEY" \
  -F "file=@docs/handbook.md"
# → {"filename": "handbook.md", "chunks_ingested": 14}
```

Supported extensions: `.txt`, `.md`, `.rst`, `.py`, `.js`, `.ts`, `.go`, `.rb`, `.java`, `.rs`, `.c`, `.cpp`,
`.cs`, `.php`, `.swift`, `.kt`, `.scala`, `.sh`. Re-uploading the same filename replaces the existing chunks.

**Sync a GitHub or GitLab repository:**

```bash
# Incremental — skips if HEAD SHA matches the stored cursor
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "github", "repo": "myorg/backend", "token": "ghp_...", "ref": "main"}'

# Force full re-index
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "gitlab", "repo": "123", "token": "glpat-...", "host": "https://gitlab.example.com", "force": true}'
```

Returns immediately with `{"status": "started", ...}` — sync runs in the background.

**Debug retrieval:**

```bash
# Run the production hybrid search path with ranking diagnostics
curl "http://localhost:8000/internal/kb/search?q=authentication+middleware&n=5&repo=myorg/backend" \
  -H "Authorization: Bearer $MASTER_KEY"
```

Response:

```json
{
  "query": "authentication middleware",
  "threshold": 0.75,
  "results": [
    {
      "doc_id": "a4d2f98b72e6c941",
      "distance": 0.61,
      "above_threshold": false,
      "lexical_score": 1.284721,
      "fused_score": 0.032522,
      "rerank_score": null,
      "source": "myorg/backend/middleware/auth.go",
      "symbol": "AuthMiddleware",
      "doc_type": "code",
      "text_preview": "func AuthMiddleware(next http.Handler) http.Handler {..."
    }
  ]
}
```

`distance` is the dense cosine distance (lower is more similar). Lexical-only candidates may remain in the final list
even when `above_threshold` is true; `fused_score` is the production reciprocal-rank-fusion score. When a cross-encoder
is configured, `rerank_score` determines final order.

**Stats:**

```bash
curl http://localhost:8000/internal/kb/stats \
  -H "Authorization: Bearer $MASTER_KEY"
# → {"total_documents": 4821}
```

**Delete chunks for a specific source:**

```bash
curl -X DELETE "http://localhost:8000/internal/kb/source?path=myorg/backend/middleware/auth.go" \
  -H "Authorization: Bearer $MASTER_KEY"
# → {"deleted_chunks": 3, "source": "myorg/backend/middleware/auth.go"}
```

**Reset the entire knowledge base:**

```bash
curl -X DELETE http://localhost:8000/internal/kb/reset \
  -H "Authorization: Bearer $MASTER_KEY"
# → {"status": "reset", "collection": "internal_kb"}
```

This drops and recreates the ChromaDB collection. All synced SHAs are also cleared.

## Prometheus metrics

Metrics are available at `http://localhost:8000/metrics` and require
`Authorization: Bearer <PROXY_MASTER_KEY>` by default. Set `server.metrics_require_auth: false` only when a trusted
network-layer control protects the endpoint.

| Metric                              | Type      | Description                                                             |
|-------------------------------------|-----------|-------------------------------------------------------------------------|
| `relay_requests_total`              | Counter   | Total requests, labelled `model`, `status`                              |
| `relay_request_latency_seconds`     | Histogram | End-to-end latency, labelled `model`, `stream`                          |
| `relay_tokens_total`                | Counter   | Tokens consumed, labelled `model`, `token_type` (`prompt`/`completion`) |
| `relay_cost_usd_total`              | Counter   | Cumulative USD cost, labelled `model`                                   |
| `relay_cache_hits_total`            | Counter   | Cache hits, labelled `model`                                            |
| `relay_pii_entities_scrubbed_total` | Counter   | PII entities removed                                                    |
| `relay_pii_requests_affected_total` | Counter   | Requests that contained PII                                             |
| `relay_rag_retrievals_total`        | Counter   | RAG lookups, labelled `status` (`hit`/`miss`/`blocked`)                 |
| `relay_rag_chunks_retrieved`        | Histogram | Chunks retrieved per request                                            |
| `relay_rate_limit_hits_total`       | Counter   | Rate limit rejections, labelled `limit_type`                            |
| `relay_content_policy_blocks_total` | Counter   | Content policy rejections                                               |
| `relay_active_requests`             | Gauge     | Requests currently in flight                                            |

Prometheus scrapes `proxy:8000/metrics` every 15 seconds. Configure its authorization credentials with the Relay
master secret, or explicitly disable application-layer metrics authentication behind a protected internal network.
