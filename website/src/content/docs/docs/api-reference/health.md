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

Readiness probe. Returns 200 only when the database, ChromaDB when enabled, and the configured rate-limit backend are
reachable. A dependency failure returns 503.

```bash
curl http://localhost:8000/readyz
# {"status":"ready","checks":{"database":"ok","vector_store":"ok","rate_limiter":"ok"}}
```

Used by Kubernetes `readinessProbe`. Traffic is not routed to a pod until this returns 200.

## GET /metrics

Prometheus text format metrics. The master key is required by default:

```bash
curl http://localhost:8000/metrics \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Key metrics exposed:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `relay_requests_total` | Counter | `model`, `status` | Total inference requests |
| `relay_request_latency_seconds` | Histogram | `model`, `stream` | End-to-end request latency |
| `relay_tokens_total` | Counter | `model`, `token_type` | Tokens consumed (`prompt`/`completion`) |
| `relay_rate_limit_hits_total` | Counter | `limit_type` | Rate limit rejections |
| `relay_cache_hits_total` | Counter | `model` | Cache hits |
| `relay_pii_entities_scrubbed_total` | Counter | — | PII entities scrubbed |
| `relay_pii_requests_affected_total` | Counter | — | Requests containing PII |
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

The generated `ServiceMonitor` reads the bearer credential from Relay's master-key Secret when
`config.metricsRequireAuth` is enabled.

### Manual scrape config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llm-proxy
    static_configs:
      - targets: ["proxy.internal:8000"]
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/relay-master-key
```
