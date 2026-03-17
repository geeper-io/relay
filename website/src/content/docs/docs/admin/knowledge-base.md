---
title: Knowledge base
description: Ingest documents and code repositories into ChromaDB for automatic RAG context injection.
---

The knowledge base is a ChromaDB collection that the proxy queries on every request (when RAG is enabled). You can populate it by uploading individual files or by syncing entire GitHub and GitLab repositories.

## Supported file types

| Type | Extensions | Chunking |
|---|---|---|
| Docs | `.txt`, `.md`, `.rst` | Word-based sliding window |
| Code | `.py`, `.js`, `.ts`, `.go`, `.rb`, `.java`, `.rs`, `.c`, `.cpp`, `.cs`, `.php`, `.swift`, `.kt`, `.scala`, `.sh` | AST-aware (tree-sitter) — one chunk per top-level function/class |

## Upload a file

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -F "file=@./runbook.md"
```

Response:

```json
{
  "filename": "runbook.md",
  "chunks_ingested": 24
}
```

Re-uploading the same filename replaces the existing chunks (old ones are deleted first).

## Sync a repository

Relay can index an entire GitHub or GitLab repository and keep it up to date incrementally.

```bash
# GitHub
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"github","repo":"myorg/backend","token":"ghp_...","ref":"main"}'

# GitLab
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"gitlab","repo":"123","token":"glpat-...","host":"https://gitlab.com"}'
```

Returns immediately — sync runs in the background. Each sync:

1. Fetches the HEAD commit SHA — skips everything if it matches the stored cursor
2. On first run: indexes the full tree
3. On subsequent runs: only changed, added, and removed files (via the compare API)
4. Saves the cursor only when all files succeed — partial runs retry from the same point on the next call

To force a full re-index regardless of the stored cursor:

```bash
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"github","repo":"myorg/backend","token":"ghp_...","force":true}'
```

### Auto-sync via config

Configure repositories to sync automatically on startup (or via CronJob):

```yaml
code_review:
  sync_on_startup: true
  github:
    token: "ghp_..."
    include:
      - myorg/backend
      - myorg/frontend
  gitlab:
    token: "glpat-..."
    include:
      - "123"
```

### Kubernetes CronJob

```yaml
syncJob:
  enabled: true
  schedule: "0 * * * *"   # hourly
```

Set `code_review.sync_on_startup: false` when using the CronJob to avoid double-syncing on pod restarts.

## Debug retrieval

Run a raw vector search to see distances and whether chunks pass the threshold. Useful for tuning `score_threshold`.

```bash
curl "http://localhost:8000/internal/kb/search?q=auth+middleware&n=5&repo=myorg/backend" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "query": "auth middleware",
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

`above_threshold: false` means the chunk passes the filter and would be injected into context.

## Stats

```bash
curl http://localhost:8000/internal/kb/stats \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

```json
{ "total_documents": 4821 }
```

## Delete a source

Remove all chunks for a specific file or repository path:

```bash
curl -X DELETE "http://localhost:8000/internal/kb/source?path=myorg/backend/middleware/auth.go" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Reset

Deletes the entire collection and recreates it empty (also clears all stored sync cursors):

```bash
curl -X DELETE http://localhost:8000/internal/kb/reset \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Kubernetes seed job

To pre-populate the knowledge base on first deploy with static docs:

```yaml
# kb-ingest-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: kb-ingest
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: ingest
          image: curlimages/curl:latest
          command:
            - sh
            - -c
            - |
              for f in /docs/*.md; do
                curl -sf -X POST http://relay:8000/internal/kb/upload \
                  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
                  -F "file=@$f"
              done
          env:
            - name: PROXY_MASTER_KEY
              valueFrom:
                secretKeyRef:
                  name: relay-master-key
                  key: PROXY_MASTER_KEY
          volumeMounts:
            - name: docs
              mountPath: /docs
      volumes:
        - name: docs
          configMap:
            name: relay-docs
```

For repository indexing, use the sync CronJob instead — it handles incremental updates automatically.
