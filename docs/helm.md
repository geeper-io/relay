# Helm reference

## Production values file

Rather than `--set` flags, use a `values-prod.yaml` for production:

```yaml
# values-prod.yaml
replicaCount: 1   # see note below about scaling

image:
  repository: your-registry.example.com/relay
  tag: "1.2.3"

secrets:
  create: false
  existingSecret: relay-secrets   # pre-created via Vault, Sealed Secrets, etc.

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: relay.internal.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: relay-tls
      hosts:
        - relay.internal.example.com

resources:
  requests:
    cpu: "1"
    memory: 2Gi
  limits:
    cpu: "4"
    memory: 4Gi

postgresql:
  auth:
    existingSecret: relay-postgresql-secret
  primary:
    persistence:
      size: 50Gi

redis:
  enabled: true   # enables shared rate limiting + caching across workers

prometheus:
  serviceMonitor:
    enabled: true
    labels:
      release: prometheus   # match your Prometheus Operator selector
```

```bash
helm upgrade --install relay helm/relay \
  --namespace relay --create-namespace \
  -f values-prod.yaml
```

## Scaling beyond one replica

The embedded ChromaDB instance writes to the local filesystem (`/app/chroma_data`). Scaling to multiple replicas
requires either:

- **ReadWriteMany storage** (NFS, EFS, Azure Files, GCS Fuse) — set `persistence.chroma.accessMode: ReadWriteMany` and
  pick a compatible `storageClass`
- **External ChromaDB server** — disable RAG (`config.rag.enabled: false`) or swap the vector store for a
  network-accessible alternative

For CPU-level concurrency without multiple replicas, increase `config.workers` (uvicorn processes within a single pod).

## Database migrations

Relay applies Alembic migrations before application initialization. Multiple workers or replicas serialize migration
work with a PostgreSQL advisory lock, so only one process changes the schema while the others wait and verify the same
head revision. Existing databases from pre-Alembic releases are adopted without recreating tables.

For change-controlled clusters, run the image as a one-off Job before the rollout:

```yaml
command: ["python", "-m", "app.db.migrate", "upgrade"]
```

Give the Job the same `DATABASE_URL` and application version as the target Relay Deployment. Back up PostgreSQL before
the upgrade, especially before migrations that remove or rewrite data.

## Evaluation Job and CronJob

The chart can run the evaluation harness inside the cluster against the release's ClusterIP Service. Retrieval mode
uses the chart-managed master-key Secret; generation mode requires a dedicated Relay API-key Secret. The workload is
created automatically when `evaluations.cases` or `evaluations.existingConfigMap` is configured. Set
`evaluations.enabled: false` explicitly to temporarily suppress it while retaining the configuration.

Run retrieval evaluations as a release-gating Job:

```yaml
evaluations:
  workload: Job
  mode: retrieval
  config:
    k: 5
    minimum_recall: 1.0
  cases: |
    {"id":"auth","query":"Where is authentication implemented?","relevant_ids":["a4d2f98b72e6c941"]}
```

```bash
helm upgrade --install relay helm/relay --namespace relay \
  -f values-prod.yaml -f evaluation-values.yaml \
  --wait --wait-for-jobs
kubectl -n relay logs -l app.kubernetes.io/component=evaluator --tail=-1
```

Job mode is a `post-install,post-upgrade` Helm hook by default. With `--wait`, it runs after the Relay rollout; a failed
evaluation fails the Helm operation. Job names include the release revision and a configuration checksum. An init
container also waits for Relay readiness. The JSON report is emitted to pod logs and the evaluator's exit code becomes
Job status. Set `evaluations.jobHook: false` only when a chart-managed, non-gating Job is intentional.

For scheduled end-to-end model evaluation, first store a dedicated Relay key:

```bash
kubectl -n relay create secret generic relay-evaluation-api-key \
  --from-literal=RELAY_API_KEY='gr-...'
```

```yaml
evaluations:
  workload: CronJob
  mode: generation
  schedule: "0 3 * * *"
  apiKeySecret: relay-evaluation-api-key
  config:
    endpoint: responses
    deployments: [general]
  cases: |
    {"id":"capital","input":"Capital of Finland? City only.","expected":{"equals":"Helsinki"}}
```

Use `evaluations.existingConfigMap` instead of inline cases for GitOps-managed datasets. The ConfigMap must contain the
configured `configKey` and `datasetKey`. See `helm/relay/examples/evaluation-*-values.yaml` for complete examples.

## Secret management

**`PROXY_MASTER_KEY`** lives in its own dedicated secret (`<release>-master-key`) and is never accepted as a plain-text
value. On first install the chart generates a random 32-character key. On every subsequent `helm upgrade` the existing
value is read back from the cluster via `lookup()` and reused — the key is never rotated unless you explicitly delete
the secret. `helm uninstall` also leaves the secret behind (`helm.sh/resource-policy: keep`) so a reinstall picks it up
unchanged.

To bring your own master key (Vault, Sealed Secrets, External Secrets Operator, etc.):

```yaml
secrets:
  existingMasterKeySecret: my-master-key-secret   # must contain key: PROXY_MASTER_KEY
```

To retrieve the auto-generated key:

```bash
kubectl get secret --namespace relay my-release-relay-master-key \
  -o jsonpath="{.data.PROXY_MASTER_KEY}" | base64 -d
```

**API keys** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) are in a separate secret. For production, manage them
externally and set:

```yaml
secrets:
  create: false
  existingSecret: relay-api-keys
```

The external Secret must contain: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL` (if not using the bundled
PostgreSQL), plus any optional keys (`GOOGLE_CLIENT_ID`, `LANGFUSE_PUBLIC_KEY`, etc.).

MCP server credentials use a separate optional Secret. Each key becomes an environment variable referenced by
`config.mcp.servers.<name>.headers_env`:

```yaml
secrets:
  mcpCredentialSecret: relay-mcp-credentials
```
