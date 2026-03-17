---
title: RAG integration
description: Automatic context injection from your knowledge base and code repositories on every request.
---

RAG (Retrieval-Augmented Generation) runs at **stage 06**. The proxy automatically enriches requests with relevant context from your knowledge base — your application code doesn't need to change.

## How it works

1. The last user message is embedded with `all-MiniLM-L6-v2` (sentence-transformers, runs locally)
2. ChromaDB is queried for the top-k chunks whose cosine distance is below the score threshold
3. Retrieved chunks are prepended to the system message before the LLM call

The prompt sent to the LLM becomes:

```
[system]
Relevant internal documentation:

[app/auth/middleware.go:AuthMiddleware]
func AuthMiddleware(next http.Handler) http.Handler { ...

---

[runbook:Deployment]
To deploy, run `make release` from the repo root ...

---

<original system message, if any>

[user]
<original user message>
```

## Configuration

```yaml
rag:
  enabled: true
  top_k: 5
  score_threshold: 0.75     # cosine distance; 0 = identical, 1 = orthogonal
                             # 0.75 is tuned for all-MiniLM-L6-v2 on mixed code + doc corpora
  embedding_model: all-MiniLM-L6-v2
```

## Chunking

Documents and code are chunked differently before embedding:

| File type | Strategy |
|---|---|
| `.txt`, `.md`, `.rst` | Word-based sliding window (~512 tokens, 50-token overlap) |
| `.py`, `.js`, `.ts`, `.go`, `.rb`, `.java`, `.rs`, `.c`, `.cpp`, `.cs`, `.php`, `.swift`, `.kt`, `.scala`, `.sh` | AST-aware (tree-sitter) — each top-level function and class is its own chunk |

AST chunking means the model receives the complete body of a relevant function rather than an arbitrary text window that may cut across boundaries. Each code chunk includes the symbol name and kind in its metadata, which surfaces in the context label (e.g. `[auth/middleware.go:AuthMiddleware]`).

## Scoping to a repository

Pass `X-Relay-Repo: owner/repo` to restrict retrieval to chunks from a specific indexed repository:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gr-..." \
  -H "X-Relay-Repo: myorg/backend" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"How does auth work?"}]}'
```

Without this header all indexed content is searched across all sources.

## Ingesting content

Upload individual files via the admin API:

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -F "file=@./runbook.md"
```

Sync a GitHub or GitLab repository (incremental, cursor-tracked):

```bash
curl -X POST http://localhost:8000/internal/kb/sync-repo \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"github","repo":"myorg/backend","token":"ghp_..."}'
```

See [Knowledge Base](/docs/admin/knowledge-base) for full details on repo sync, the CronJob, and debug endpoints.

## Storage

### Embedded (default, single replica)

ChromaDB runs inside the relay pod, persisting to a local PVC.

```yaml
persistence:
  chroma:
    size: 10Gi
    storageClass: ""
    accessMode: ReadWriteOnce
```

### Server mode (multi-replica)

ChromaDB runs as a separate Deployment. Required when `replicaCount > 1`.

```yaml
replicaCount: 3

chromadb:
  server:
    enabled: true
    persistence:
      size: 10Gi
```

See [Scaling](/docs/deployment/scaling) for the full multi-replica setup.

## Tuning retrieval

| Parameter | Effect |
|---|---|
| `top_k: 3` | Fewer chunks → less context noise, lower cost |
| `top_k: 10` | More context, but may hit `max_input_tokens` |
| `score_threshold: 0.9` | Stricter — only very close matches |
| `score_threshold: 0.5` | Broader — useful for short or vague queries |

Use the `/internal/kb/search` debug endpoint to see raw distances before adjusting the threshold:

```bash
curl "http://localhost:8000/internal/kb/search?q=auth+middleware&repo=myorg/backend" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Disabling per-request

There is no per-request override — RAG is either on or off globally. To disable for a specific use case, deploy a separate proxy instance with `rag.enabled: false`.
