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
