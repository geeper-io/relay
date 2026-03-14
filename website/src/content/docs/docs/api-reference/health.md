---
title: Health & metrics
description: Health check and Prometheus metrics endpoints.
---

## GET /healthz

Liveness probe. Returns 200 once the application has started.

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

Used by Kubernetes `livenessProbe`. The default `initialDelaySeconds: 60` accounts for spaCy `en_core_web_lg` loading (~800 MB).

## GET /readyz

Readiness probe. Returns 200 only when all dependencies (database, ChromaDB) are reachable.

```bash
curl http://localhost:8000/readyz
# {"status":"ok"}
```

Used by Kubernetes `readinessProbe`. Traffic is not routed to a pod until this returns 200.

## GET /metrics

Prometheus text format metrics.

```bash
curl http://localhost:8000/metrics
```

Key metrics exposed:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `relay_requests_total` | Counter | `model`, `status` | Total inference requests |
| `relay_request_duration_seconds` | Histogram | `model` | End-to-end request latency |
| `relay_tokens_total` | Counter | `model`, `type` | Tokens consumed (`prompt`/`completion`) |
| `relay_rate_limit_hits_total` | Counter | `limit_type` | Rate limit rejections |
| `relay_cache_hits_total` | Counter | — | Cache hits |
| `relay_pii_entities_total` | Counter | `entity_type` | PII entities scrubbed |
| `relay_content_policy_blocks_total` | Counter | — | Content policy rejections |

### Kubernetes ServiceMonitor

Enable automatic scraping with Prometheus Operator:

```yaml
# values.yaml
prometheus:
  serviceMonitor:
    enabled: true
    interval: "15s"
    scrapeTimeout: "10s"
    labels:
      release: prometheus   # match your Prometheus Operator release label
```

### Manual scrape config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llm-proxy
    static_configs:
      - targets: ["proxy.internal:8000"]
    metrics_path: /metrics
```
