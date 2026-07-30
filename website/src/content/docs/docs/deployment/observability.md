---
title: Observability
description: Prometheus scrape config, Grafana dashboards, and structured logging.
---

## Prometheus

### Manual scrape config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llm-proxy
    static_configs:
      - targets: ["proxy.internal:8000"]
    metrics_path: /metrics
    scrape_interval: 15s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/relay-master-key
```

`/metrics` requires `PROXY_MASTER_KEY` by default. Mount it as a Prometheus secret rather than placing it directly in
the scrape configuration. The Helm `ServiceMonitor` configures this Secret reference automatically.

### Kubernetes ServiceMonitor (Prometheus Operator)

```yaml
prometheus:
  serviceMonitor:
    enabled: true
    interval: "15s"
    scrapeTimeout: "10s"
    labels:
      release: prometheus    # must match your Prometheus Operator's serviceMonitorSelector
```

### Key metrics

| Metric | Type | Labels |
|---|---|---|
| `relay_requests_total` | Counter | `model`, `status` |
| `relay_request_latency_seconds` | Histogram | `model`, `stream` |
| `relay_tokens_total` | Counter | `model`, `token_type` (prompt/completion) |
| `relay_rate_limit_hits_total` | Counter | `limit_type` |
| `relay_cache_hits_total` | Counter | `model` |
| `relay_pii_entities_scrubbed_total` | Counter | — |
| `relay_content_policy_blocks_total` | Counter | — |
| `relay_routing_decisions_total` | Counter | `deployment`, `policy_version`, `endpoint` |

## OpenTelemetry

Enable OTLP/HTTP trace export:

```yaml
telemetry:
  enabled: true
  service_name: geeper-relay
  otlp_endpoint: https://otel-collector.internal/v1/traces
  otlp_headers:
    X-Tenant: relay
  sample_ratio: 0.25
```

Relay instruments inbound FastAPI requests and outbound HTTPX calls. Inference spans include requested/physical model,
deployment alias, policy version, endpoint, user ID, and team ID. The active OpenTelemetry trace/span IDs are also
attached to Langfuse metadata when both integrations are enabled.
Supply sensitive exporter credentials through the `TELEMETRY__OTLP_HEADERS` environment variable or your secret
management layer rather than committing them to YAML.

## Grafana

### Suggested dashboard panels

1. **Request rate** (requests/sec by model)
   ```yaml
   sum by (model) (rate(relay_requests_total[5m]))
   ```

2. **Error rate**
   ```yaml
   sum by (status) (rate(relay_requests_total{status!="success"}[5m]))
   ```

3. **Latency p50 / p95 / p99**
   ```yaml
   histogram_quantile(0.95, sum by (le) (rate(relay_request_latency_seconds_bucket[5m])))
   ```

4. **Token throughput**
   ```yaml
   sum by (token_type) (rate(relay_tokens_total[5m]))
   ```

5. **Rate limit hit rate**
   ```yaml
   sum by (limit_type) (rate(relay_rate_limit_hits_total[5m]))
   ```

6. **Cache hit ratio**
   ```yaml
   rate(relay_cache_hits_total[5m]) / rate(relay_requests_total[5m])
   ```

7. **PII entities scrubbed**
   ```yaml
   rate(relay_pii_entities_scrubbed_total[5m])
   ```

### Recommended alerts

```yaml
groups:
  - name: llm-proxy
    rules:
      - alert: HighErrorRate
        expr: sum(rate(relay_requests_total{status="error"}[5m])) > 0.05
        for: 5m
        annotations:
          summary: "High upstream error rate"

      - alert: HighLatency
        expr: histogram_quantile(0.95, sum by (le) (rate(relay_request_latency_seconds_bucket[5m]))) > 10
        for: 10m
        annotations:
          summary: "p95 latency over 10s"

      - alert: RateLimitSpike
        expr: sum(rate(relay_rate_limit_hits_total[5m])) > 5
        for: 5m
        annotations:
          summary: "Elevated rate limiting — check user quotas"
```

## Structured logging

Enable JSON logging for log aggregation (Loki, CloudWatch, Datadog):

```yaml
server:
  log_level: info
  # JSON format emitted automatically when LOG_FORMAT=json env var is set
```

Each request logs:

```json
{
  "timestamp": "2025-01-01T00:00:00Z",
  "level": "info",
  "request_id": "req_01j...",
  "user_id": "user_01j...",
  "team_id": "team_01j...",
  "model": "gpt-4o",
  "prompt_tokens": 142,
  "completion_tokens": 87,
  "latency_ms": 1240,
  "cached": false,
  "pii_entities_scrubbed": 2
}
```

### Loki (Kubernetes)

Add Promtail or the Grafana Alloy agent to your cluster and configure log labels:

```yaml
# promtail pipeline stage
- match:
    selector: '{app="llm-proxy"}'
    stages:
      - json:
          expressions:
            model: model
            user_id: user_id
      - labels:
          model:
          user_id:
```

This enables log queries like `{app="llm-proxy", model="gpt-4o"}`.

## Langfuse traces

For per-request prompt/completion tracing see [Analytics & observability](/docs/features/analytics).

## Health endpoints

Used by Kubernetes probes:

| Endpoint | Purpose | Returns 200 when |
|---|---|---|
| `GET /healthz` | Liveness | App started |
| `GET /readyz` | Readiness | DB, enabled ChromaDB, and configured rate-limit backend reachable |
