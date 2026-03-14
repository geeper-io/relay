---
title: Knowledge base
description: Ingest documents into ChromaDB for automatic RAG context injection.
---

The knowledge base is a ChromaDB collection that the proxy queries on every request (when RAG is enabled). You populate it by ingesting documents via the admin API.

## Supported file types

`.txt`, `.md`, `.rst`

## Ingest a directory

Recursively scans a directory and ingests all supported files:

```bash
curl -X POST http://localhost:8000/internal/kb/ingest-directory \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"directory": "knowledge_base"}'
```

The `directory` path is relative to the proxy container's working directory (`/app` in Docker). Mount your documents at `/app/knowledge_base` or update the path.

Response:

```json
{
  "ingested_files": 8,
  "total_chunks": 212,
  "files": [
    "knowledge_base/api.md",
    "knowledge_base/guide.md",
    "knowledge_base/faq.md"
  ]
}
```

## Upload a single file

```bash
curl -X POST http://localhost:8000/internal/kb/upload \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -F "file=@./runbook.md"
```

## CLI script

For local development or CI:

```bash
python scripts/ingest_kb.py --directory ./docs
```

## Chunking

Documents are split into overlapping chunks before embedding. Default chunk size and overlap are configured in the source. Chunks that are too short (< 50 characters) are skipped.

## Kubernetes

Mount the knowledge base files as a PVC and run ingestion as a Job:

```yaml
# knowledge-base-ingest-job.yaml
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
          image: ghcr.io/your-org/llm-proxy:latest
          command: ["python", "scripts/ingest_kb.py", "--directory", "/app/knowledge_base"]
          env:
            - name: PROXY_MASTER_KEY
              valueFrom:
                secretKeyRef:
                  name: llm-proxy-master-key
                  key: PROXY_MASTER_KEY
          volumeMounts:
            - name: knowledge-base
              mountPath: /app/knowledge_base
      volumes:
        - name: knowledge-base
          persistentVolumeClaim:
            claimName: llm-proxy-knowledge-base
```

:::tip
Run the Job after deploying a new version of your docs. The ChromaDB PVC persists across pod restarts — you only need to re-ingest when content changes.
:::

## Re-ingestion

Re-ingesting a file that already exists in the collection adds duplicate chunks. Before re-ingesting:

1. Use the ChromaDB HTTP API to delete the collection, or
2. Scale the proxy to 0 replicas, delete the `chroma_data` PVC data, then re-ingest

A proper upsert/deduplication API is planned for a future release.
