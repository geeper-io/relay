---
title: RAG integration
description: Automatic context injection from a ChromaDB knowledge base on every request.
---

RAG (Retrieval-Augmented Generation) runs at **stage 06**. The proxy automatically enriches requests with relevant context from your knowledge base — your application code doesn't need to change.

## How it works

1. The last user message is embedded with `all-MiniLM-L6-v2` (sentence-transformers, ~80 MB)
2. ChromaDB is queried for the top-k chunks above the score threshold
3. Retrieved chunks are prepended to the system message before the LLM call

The prompt sent to the LLM becomes:

```
[system]
Relevant context:
---
<chunk 1 text>
---
<chunk 2 text>
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
  score_threshold: 0.4
  embedding_model: all-MiniLM-L6-v2
```

## Ingesting documents

### From a directory

```bash
curl -X POST http://localhost:8000/internal/kb/ingest-directory \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"directory": "knowledge_base"}'
```

The `directory` path is relative to the proxy's working directory. Supported formats: `.txt`, `.md`, `.rst`.

Response:

```json
{
  "ingested_files": 12,
  "total_chunks": 348,
  "files": ["docs/api.md", "docs/guide.md", ...]
}
```

### Single file upload

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -F "file=@./runbook.md"
```

### CLI script

```bash
python scripts/ingest_kb.py --directory ./docs
```

## Storage

ChromaDB persists to disk at `chroma_data/` (configurable). In Kubernetes this maps to a PVC:

```yaml
persistence:
  chroma:
    size: 10Gi
    storageClass: ""      # cluster default
    accessMode: ReadWriteOnce
```

:::caution
`ReadWriteOnce` only works with `replicaCount: 1`. For multi-replica deployments, use `ReadWriteMany` (NFS, EFS, Azure Files). See [Scaling](/docs/deployment/scaling).
:::

## Tuning retrieval

| Parameter | Effect |
|---|---|
| `top_k: 3` | Fewer chunks → less context noise, lower cost |
| `top_k: 10` | More context, but may hit `max_input_tokens` |
| `score_threshold: 0.6` | Higher = stricter matching, fewer false positives |
| `score_threshold: 0.2` | Very broad matching — useful for short queries |

## Disabling per-request

There is no per-request override — RAG is either on or off globally. To disable for a specific use case, deploy a separate proxy instance with `rag.enabled: false`.
