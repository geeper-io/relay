---
title: Code review
description: Index GitHub and GitLab repositories so the model reviews diffs against your actual codebase.
---

Relay can index your source repositories and inject relevant context whenever you send a diff for review. Instead of generic feedback, the model sees the actual functions, types, and patterns your diff touches.

## How it works

1. Relay syncs a repository into ChromaDB — each file is chunked by AST (one chunk per top-level function/class)
2. You send a diff as a chat message with `X-Relay-Repo: owner/repo`
3. Relay retrieves the most semantically similar chunks from that repo and prepends them as context
4. The model reviews the diff with knowledge of your conventions, not just the changed lines

## Setup

### 1. Index a repository

```bash
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"github","repo":"myorg/backend","token":"ghp_...","ref":"main"}'
```

Or configure auto-sync so Relay keeps the index up to date:

```yaml
# config/config.yaml
code_review:
  sync_on_startup: true
  github:
    token: "ghp_..."
    include:
      - myorg/backend
```

### 2. Send a diff for review

```bash
git diff | jq -Rs '{
  model: "gpt-4o",
  messages: [{"role":"user","content":("Review this diff:\n\n" + .)}]
}' | curl -s http://localhost:8000/v1/chat/completions \
     -H "Authorization: Bearer gr-..." \
     -H "Content-Type: application/json" \
     -H "X-Relay-Repo: myorg/backend" \
     -d @- | jq -r '.choices[0].message.content'
```

The `X-Relay-Repo` header scopes retrieval to that repository only. Without it, all indexed content is searched.

## What the model receives

The LLM prompt includes the diff plus the most relevant chunks from the indexed codebase:

```
[system]
Relevant internal documentation:

[auth/middleware.go:AuthMiddleware]
func AuthMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        ...
    })
}

---

[auth/tokens.go:ValidateToken]
func ValidateToken(token string) (*Claims, error) {
    ...
}

---

[user]
Review this diff:

diff --git a/auth/middleware.go b/auth/middleware.go
...
```

## Pipeline interaction

| Stage | Behaviour |
|---|---|
| PII scrubbing | Diffs bypass scrubbing — identifiers and class names produce too many false positives |
| RAG | The diff text is the retrieval query; `X-Relay-Repo` scopes results to the named repo |
| LLM call | Model receives diff + retrieved context and returns a review grounded in your codebase |

## Incremental sync

Relay tracks each repository's HEAD SHA. Subsequent syncs only re-index files that changed — dramatically faster than full re-ingestion for large repos.

The cursor is saved only when all files in a sync succeed. If some files fail (e.g. rate-limited), the next sync will retry from the same point rather than skipping ahead.

## Kubernetes CronJob

For teams that want continuous index freshness without syncing on pod startup:

```yaml
# values.yaml
syncJob:
  enabled: true
  schedule: "0 * * * *"   # hourly

config:
  code_review:
    sync_on_startup: false
    github:
      token: "ghp_..."
      include:
        - myorg/backend
```

See [Knowledge Base](/docs/admin/knowledge-base) for the full sync API reference, debug endpoint, and GitLab configuration.
