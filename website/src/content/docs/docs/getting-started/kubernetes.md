---
title: Kubernetes (Helm)
description: Deploy Geeper Relay to Kubernetes with the production Helm chart.
---

## Prerequisites

- `kubectl` configured against your cluster
- Helm ≥ 3.8
- A cluster with default StorageClass that supports ReadWriteOnce PVCs

## 1. Add the Bitnami chart repository

The Helm chart uses Bitnami subcharts for PostgreSQL and Redis:

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
```

## 2. Fetch chart dependencies

```bash
helm dependency build ./helm/relay
```

This downloads the Bitnami PostgreSQL and Redis charts into `helm/relay/charts/`.

## 3. Install

Minimal install with OpenAI key and an Ingress:

```bash
helm install relay ./helm/relay \
  --set secrets.openaiApiKey=$OPENAI_API_KEY \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=proxy.internal \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

What gets created:
- `Deployment` — proxy pods
- `Service` (ClusterIP on 8000)
- `Ingress`
- `Secret` (`<release>-llm-proxy`) — API keys and provider credentials
- `Secret` (`<release>-master-key`) — auto-generated `PROXY_MASTER_KEY`
- `PersistentVolumeClaim` × 2 — ChromaDB data (10 Gi) and knowledge base (5 Gi)
- Bitnami PostgreSQL StatefulSet + PVC (20 Gi)

## PROXY_MASTER_KEY auto-generation

On first install the chart generates a random 32-character key and stores it in a dedicated Secret. On every subsequent `helm upgrade`, the chart reads the existing Secret via `lookup()` so the key is never rotated unintentionally. The Secret has `helm.sh/resource-policy: keep` — `helm uninstall` leaves it in place so a reinstall picks up the same key.

Retrieve the master key after installation:

```bash
kubectl get secret llm-proxy-master-key -o jsonpath='{.data.PROXY_MASTER_KEY}' | base64 -d
```

## 4. Create your first API key

```bash
MASTER_KEY=$(kubectl get secret llm-proxy-master-key \
  -o jsonpath='{.data.PROXY_MASTER_KEY}' | base64 -d)

curl -X POST https://proxy.internal/internal/api-keys \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "team-alpha", "user_id": "alice"}'
```

## Upgrading

```bash
helm upgrade relay ./helm/relay \
  --set secrets.openaiApiKey=$OPENAI_API_KEY \
  --reuse-values
```

`--reuse-values` preserves all previously set values. The `PROXY_MASTER_KEY` is always preserved regardless — it's read from the cluster Secret, not from values.

## Production checklist

| Item | Recommended setting |
|---|---|
| Replicas | `replicaCount: 2` minimum (requires RWX storage — see [Scaling](/docs/deployment/scaling)) |
| Resources | Default `requests.memory: 1500Mi` covers spaCy model load |
| Ingress TLS | `ingress.tls` with cert-manager |
| Secrets | `secrets.create: false` + `secrets.existingSecret` from Vault/ESO |
| Redis | `redis.enabled: true` for multi-replica rate limiting and caching |
| Monitoring | `prometheus.serviceMonitor.enabled: true` |

## Common values

```yaml
# values-prod.yaml
replicaCount: 2

image:
  repository: ghcr.io/geeper-io/relay
  tag: "1.2.0"

secrets:
  create: false
  existingSecret: llm-proxy-secrets     # pre-provisioned by Vault/ESO
  existingMasterKeySecret: ""           # leave empty = auto-generate

redis:
  enabled: true

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: proxy.internal
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: llm-proxy-tls
      hosts:
        - proxy.internal

prometheus:
  serviceMonitor:
    enabled: true
```

```bash
helm upgrade --install relay ./helm/relay -f values-prod.yaml
```

See [Helm values reference](/docs/deployment/helm-reference) for the full list of options.
