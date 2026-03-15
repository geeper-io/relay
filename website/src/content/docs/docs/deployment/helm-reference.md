---
title: Helm values reference
description: Complete annotated reference for all Geeper Relay Helm chart values.
---

All values with their defaults. Override via `--set key=value` or a `-f values.yaml` file.

## Image

| Key | Default | Description |
|---|---|---|
| `image.repository` | `llm-proxy` | Container image repository |
| `image.tag` | `"latest"` | Image tag (overrides `Chart.appVersion`) |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes pull policy |
| `imagePullSecrets` | `[]` | Image pull secrets for private registries |
| `replicaCount` | `1` | Pod replicas. See [Scaling](/docs/deployment/scaling) for RWX requirements. |

## Proxy configuration

Non-secret settings mounted as `/app/config/config.yaml`:

| Key | Default | Description |
|---|---|---|
| `config.workers` | `4` | uvicorn worker processes |
| `config.logLevel` | `"info"` | Log level |
| `config.llm.defaultModel` | `"gpt-4o"` | Default model |
| `config.llm.allowedModels` | `[gpt-4o, gpt-4o-mini, ...]` | Model allowlist |
| `config.llm.fallbackModels` | `[]` | Fallback chain on provider error |
| `config.llm.modelAliases` | `{}` | Alias → canonical model map |
| `config.llm.perModelMaxTokens` | `{}` | Per-model output token caps |
| `config.rag.enabled` | `true` | Enable RAG |
| `config.rag.topK` | `5` | Top-k chunks |
| `config.rag.scoreThreshold` | `0.4` | Min similarity score |
| `config.rag.embeddingModel` | `"all-MiniLM-L6-v2"` | Embedding model |
| `config.pii.enabled` | `true` | Enable PII scrubbing |
| `config.pii.scoreThreshold` | `0.7` | Min Presidio confidence |
| `config.pii.entities` | `[PERSON, EMAIL_ADDRESS, ...]` | Entity types |
| `config.rateLimiting.enabled` | `true` | Enable rate limiting |
| `config.rateLimiting.backend` | `"memory"` | Auto-set to `redis` when `redis.enabled` |
| `config.rateLimiting.defaults.requestsPerMinute` | `60` | |
| `config.rateLimiting.defaults.tokensPerMinute` | `100000` | |
| `config.rateLimiting.defaults.tokensPerDay` | `1000000` | |
| `config.contentPolicy.enabled` | `true` | Enable content policy |
| `config.contentPolicy.maxInputTokens` | `32000` | Max prompt tokens |
| `config.contentPolicy.blockedPatterns` | `[...]` | Blocked phrase list |
| `config.cache.enabled` | `false` | Enable response caching |
| `config.cache.type` | `"local"` | Auto-set to `redis` when `redis.enabled` |
| `config.cache.ttl` | `3600` | Cache TTL (seconds) |
| `config.analytics.enabled` | `false` | Enable Langfuse export |
| `config.analytics.provider` | `"langfuse"` | Analytics provider |

## Secrets

| Key | Default | Description |
|---|---|---|
| `secrets.create` | `true` | Create a Secret from values below |
| `secrets.existingSecret` | `""` | Name of a pre-existing Secret (disables `secrets.create`) |
| `secrets.existingMasterKeySecret` | `""` | Bring your own master key Secret. Empty = auto-generate. |
| `secrets.openaiApiKey` | `""` | |
| `secrets.anthropicApiKey` | `""` | |
| `secrets.azureOpenaiApiKey` | `""` | |
| `secrets.azureOpenaiEndpoint` | `""` | |
| `secrets.googleClientId` | `""` | |
| `secrets.googleClientSecret` | `""` | |
| `secrets.authBaseUrl` | `""` | e.g. `https://proxy.internal` |
| `secrets.langfusePublicKey` | `""` | |
| `secrets.langfuseSecretKey` | `""` | |
| `secrets.langfuseHost` | `""` | Empty = Langfuse Cloud |

## External database

| Key | Default | Description |
|---|---|---|
| `externalDatabase.url` | `""` | Skip bundled PostgreSQL. e.g. `postgresql+asyncpg://user:pass@host:5432/llm_proxy` |

## Persistence

Used in single-replica (embedded ChromaDB) mode only. Ignored when `chromadb.server.enabled: true`.

| Key | Default | Description |
|---|---|---|
| `persistence.chroma.enabled` | `true` | Create local ChromaDB PVC (single replica only) |
| `persistence.chroma.size` | `10Gi` | |
| `persistence.chroma.storageClass` | `""` | Empty = cluster default |
| `persistence.chroma.accessMode` | `ReadWriteOnce` | |
| `persistence.knowledgeBase.enabled` | `true` | Create knowledge base PVC |
| `persistence.knowledgeBase.size` | `5Gi` | |
| `persistence.knowledgeBase.storageClass` | `""` | |
| `persistence.knowledgeBase.accessMode` | `ReadWriteOnce` | |

## ChromaDB server (multi-replica)

When enabled, deploys a standalone ChromaDB pod that all relay replicas share via HTTP. Required for `replicaCount > 1`. See [Scaling](/docs/deployment/scaling).

| Key | Default | Description |
|---|---|---|
| `chromadb.server.enabled` | `false` | Deploy ChromaDB as a separate pod |
| `chromadb.server.port` | `8001` | ChromaDB service port |
| `chromadb.server.image.repository` | `chromadb/chroma` | |
| `chromadb.server.image.tag` | `"latest"` | |
| `chromadb.server.resources.requests.cpu` | `250m` | |
| `chromadb.server.resources.requests.memory` | `512Mi` | |
| `chromadb.server.resources.limits.cpu` | `"1"` | |
| `chromadb.server.resources.limits.memory` | `2Gi` | |
| `chromadb.server.persistence.size` | `10Gi` | PVC size for the ChromaDB pod |
| `chromadb.server.persistence.storageClass` | `""` | Empty = cluster default |

## Service

| Key | Default | Description |
|---|---|---|
| `service.type` | `ClusterIP` | `ClusterIP`, `NodePort`, or `LoadBalancer` |
| `service.port` | `8000` | Service port |

## Ingress

| Key | Default | Description |
|---|---|---|
| `ingress.enabled` | `false` | |
| `ingress.className` | `""` | Ingress class name |
| `ingress.annotations` | `{}` | e.g. `cert-manager.io/cluster-issuer` |
| `ingress.hosts` | (example) | Host + path configuration |
| `ingress.tls` | `[]` | TLS configuration |

## Resources

| Key | Default | Description |
|---|---|---|
| `resources.requests.cpu` | `500m` | |
| `resources.requests.memory` | `1500Mi` | Sized for spaCy `en_core_web_lg` (~800 MB) |
| `resources.limits.cpu` | `"2"` | |
| `resources.limits.memory` | `3Gi` | |

## Autoscaling (HPA)

| Key | Default | Description |
|---|---|---|
| `autoscaling.enabled` | `false` | |
| `autoscaling.minReplicas` | `1` | |
| `autoscaling.maxReplicas` | `5` | |
| `autoscaling.targetCPUUtilizationPercentage` | `70` | |

## Probes

| Key | Default | Description |
|---|---|---|
| `livenessProbe.initialDelaySeconds` | `60` | Allow time for spaCy model to load |
| `readinessProbe.initialDelaySeconds` | `60` | |

## Prometheus

| Key | Default | Description |
|---|---|---|
| `prometheus.serviceMonitor.enabled` | `false` | Create `ServiceMonitor` for Prometheus Operator |
| `prometheus.serviceMonitor.interval` | `"15s"` | Scrape interval |
| `prometheus.serviceMonitor.namespace` | `""` | Empty = release namespace |
| `prometheus.serviceMonitor.labels` | `{}` | Labels to match Prometheus Operator |

## Bundled PostgreSQL (Bitnami)

| Key | Default | Description |
|---|---|---|
| `postgresql.enabled` | `true` | Deploy bundled PostgreSQL |
| `postgresql.auth.username` | `proxy` | |
| `postgresql.auth.password` | `""` | Auto-generated when empty |
| `postgresql.auth.database` | `llm_proxy` | |
| `postgresql.primary.persistence.size` | `20Gi` | |

Pass-through to `bitnami/postgresql` chart. See [Bitnami docs](https://github.com/bitnami/charts/tree/main/bitnami/postgresql) for all options.

## Bundled Redis (Bitnami)

| Key | Default | Description |
|---|---|---|
| `redis.enabled` | `false` | Deploy bundled Redis |
| `redis.auth.enabled` | `false` | Enable Redis auth |
| `redis.master.persistence.size` | `2Gi` | |

When `redis.enabled: true`, the proxy automatically uses Redis for rate limiting and caching.
