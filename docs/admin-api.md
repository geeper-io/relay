# Admin API

All admin endpoints require `Authorization: Bearer <PROXY_MASTER_KEY>`.

## User and key management

```bash
# Create a team
curl -X POST "http://localhost:8000/internal/teams?name=engineering" \
  -H "Authorization: Bearer $MASTER_KEY"

# Create a user
curl -X POST "http://localhost:8000/internal/users?external_id=bob@company.com&team_id=<team-id>" \
  -H "Authorization: Bearer $MASTER_KEY"

# Issue an API key
curl -X POST "http://localhost:8000/internal/api-keys?user_id=<user-id>&name=laptop" \
  -H "Authorization: Bearer $MASTER_KEY"
# Returns: { "key": "gr-...", "key_prefix": "gr-XXXXXX", "id": "..." }
# The raw key is shown once and not stored.
```

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
# Run a raw vector search; shows distances and threshold pass/fail per chunk
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
      "distance": 0.61,
      "above_threshold": false,
      "source": "myorg/backend/middleware/auth.go",
      "symbol": "AuthMiddleware",
      "doc_type": "code",
      "text_preview": "func AuthMiddleware(next http.Handler) http.Handler {..."
    }
  ]
}
```

`above_threshold: false` means the chunk passes the filter and would be injected into context. `distance` is cosine
distance (0 = identical, 1 = orthogonal); lower is more similar.

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

Metrics are available at `http://localhost:8000/metrics`.

| Metric                              | Type      | Description                                                             |
|-------------------------------------|-----------|-------------------------------------------------------------------------|
| `relay_requests_total`              | Counter   | Total requests, labelled `model`, `status`                              |
| `relay_request_latency_seconds`     | Histogram | End-to-end latency, labelled `model`, `stream`                          |
| `relay_tokens_total`                | Counter   | Tokens consumed, labelled `model`, `token_type` (`prompt`/`completion`) |
| `relay_cost_usd_total`              | Counter   | Cumulative USD cost, labelled `model`                                   |
| `relay_cache_hits_total`            | Counter   | Cache hits, labelled `model`                                            |
| `relay_pii_entities_scrubbed_total` | Counter   | PII entities removed                                                    |
| `relay_pii_requests_affected_total` | Counter   | Requests that contained PII                                             |
| `relay_rag_retrievals_total`        | Counter   | RAG lookups, labelled `status` (`hit`/`miss`)                           |
| `relay_rag_chunks_retrieved`        | Histogram | Chunks retrieved per request                                            |
| `relay_rate_limit_hits_total`       | Counter   | Rate limit rejections, labelled `limit_type`                            |
| `relay_content_policy_blocks_total` | Counter   | Content policy rejections                                               |
| `relay_active_requests`             | Gauge     | Requests currently in flight                                            |

Prometheus scrapes `proxy:8000/metrics` every 15 seconds (configured in `docker/prometheus.yml`).
