---
title: Scaling & HA
description: Horizontal scaling, HPA, and ChromaDB server mode for multi-replica deployments.
---

## Single-replica (default)

The default `replicaCount: 1` with `config.workers: 4` provides concurrency via multiple uvicorn worker processes. This is the recommended starting point.

For most teams, a single replica with a Redis backend for rate limiting and caching is sufficient up to several hundred requests per minute.

## Multi-replica

Running multiple relay replicas requires two things: a shared ChromaDB and shared state for rate limiting/caching.

### ChromaDB server mode

By default, ChromaDB runs embedded in the relay pod and writes to a local PVC — this only works with a single pod. For `replicaCount > 1`, enable the ChromaDB server deployment so all relay pods share one vector store:

```yaml
replicaCount: 3

chromadb:
  server:
    enabled: true        # deploys a separate chromadb pod with its own PVC
    persistence:
      size: 10Gi
      storageClass: ""   # cluster default
```

This creates a `chromadb` Deployment + Service + PVC. Relay pods automatically connect to it via HTTP — no other config needed.

:::tip
The ChromaDB server pod uses `ReadWriteOnce` storage (single pod, always). The relay pods are now fully stateless with respect to the vector store.
:::

### Redis required for shared state

With multiple replicas, the rate limiter and cache **must** use Redis — otherwise each pod enforces limits independently and caches independently:

```yaml
redis:
  enabled: true
```

### Full multi-replica example

```yaml
replicaCount: 3

chromadb:
  server:
    enabled: true

redis:
  enabled: true

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 8
  targetCPUUtilizationPercentage: 70
```

## HPA (Horizontal Pod Autoscaler)

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 8
  targetCPUUtilizationPercentage: 70
```

Scaling is driven by CPU utilisation. Memory-based scaling is less useful here because the spaCy model is loaded once at startup and contributes a constant baseline (~800 MB).

:::tip
The 60-second `initialDelaySeconds` on liveness and readiness probes accounts for spaCy model loading. Scaling events create new pods that need ~60 seconds before they receive traffic — factor this into your `minReplicas` for bursty workloads.
:::

## Memory sizing

The spaCy `en_core_web_lg` model loads ~800 MB on startup. Default resource requests account for this:

```yaml
resources:
  requests:
    memory: 1500Mi   # 800 MB spaCy + headroom for requests
  limits:
    memory: 3Gi      # headroom for concurrent request processing
```

Do not reduce `requests.memory` below 1 Gi — the pod will be OOMKilled during model load.

## PostgreSQL

The bundled Bitnami PostgreSQL subchart deploys a single Primary. For production HA:

1. Set `postgresql.enabled: false`
2. Provision an external HA PostgreSQL (RDS Multi-AZ, Cloud SQL, etc.)
3. Set `externalDatabase.url: postgresql+asyncpg://user:pass@host:5432/llm_proxy`

## Pod disruption budget

For zero-downtime rolling updates with `replicaCount >= 2`:

```yaml
# pdb.yaml — apply separately
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: relay-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: relay
```
