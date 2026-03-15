---
title: Knowledge base
description: Ingest documents into ChromaDB for automatic RAG context injection.
---

The knowledge base is a ChromaDB collection that the proxy queries on every request (when RAG is enabled). You populate it by uploading documents via the admin API.

## Supported file types

`.txt`, `.md`, `.rst`

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

## Bulk ingest

Upload files one at a time from a local directory using a shell loop:

```bash
for f in ./docs/*.md; do
  curl -s -X POST http://relay.company.com/internal/kb/upload \
    -H "Authorization: Bearer $PROXY_MASTER_KEY" \
    -F "file=@$f" | jq .
done
```

## Stats

```bash
curl http://localhost:8000/internal/kb/stats \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

```json
{ "total_documents": 348 }
```

## Reset

Deletes all documents from the collection:

```bash
curl -X DELETE http://localhost:8000/internal/kb/reset \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

## Chunking

Documents are split into overlapping ~512-token chunks before embedding. Chunks shorter than a few words are skipped automatically.

## Re-ingestion

Uploading the same file again adds duplicate chunks. Reset the collection first, then re-upload all files to do a clean re-ingest.

## Kubernetes

Use a `curl` init container or a Job to seed the knowledge base after deploy:

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
            name: relay-docs   # or a PVC / S3 init container
```
