---
title: Model evaluation harness
description: Compare Relay deployment aliases with repeatable JSONL cases and deterministic graders.
---

The repository includes a lightweight evaluation runner for comparing logical deployments through the same policies
and gateway pipeline used in production.

```bash
export RELAY_API_KEY=gr-...
python -m evals.run --config evals/config.example.yaml
```

The YAML configuration selects `/v1/responses` or `/v1/chat/completions`, deployment aliases, concurrency, timeout,
dataset, and output report. JSONL cases support `equals`, `contains`, `not_contains`, `citations`, `regex`, and
`json_keys` graders. Negative checks are useful for PII/secret leakage and indirect-injection cases:

```json
{"id":"capital","input":"Capital of Finland? City only.","expected":{"equals":"Helsinki"}}
{"id":"no-secret","input":"Summarize the credential policy.","expected":{"not_contains":["sk-test-secret"],"citations":["security-runbook"]}}
```

The JSON report contains every output and error plus pass rate, mean score, average/p95 latency, token totals, actual
routed deployment, and policy version for each requested deployment. The command exits non-zero if any case fails, so
it can gate policy/model changes in CI.

Evaluation reports contain model outputs and should be handled as potentially sensitive artifacts.

## Retrieval evaluation

Retrieval mode calls the same hybrid ranking path used by live RAG requests and reports recall@k, MRR, nDCG@k, and
latency. Replace the example relevant IDs with stable chunk IDs from `/internal/kb/search`:

```bash
export PROXY_MASTER_KEY=...
python -m evals.run --config evals/retrieval-config.example.yaml
```

```json
{"id":"auth-middleware","query":"Where is authentication middleware implemented?","relevant_ids":["a4d2f98b72e6c941"],"minimum_recall":1.0}
```

The command exits non-zero when a case misses its minimum recall, so a curated retrieval set can gate embedding,
chunking, fusion-weight, threshold, or reranker changes in CI.

## Kubernetes Job or CronJob

The Helm chart runs the same harness as an in-cluster `Job` or `CronJob`. It generates the evaluator configuration and
JSONL dataset as a ConfigMap, connects through the release's ClusterIP Service, waits for Relay readiness, reads
credentials from Secrets, writes the full JSON report to pod logs, and exposes success or failure as workload status.
Job mode is a post-install/post-upgrade hook by default, so `helm upgrade --wait` evaluates the rolled-out release and
fails the Helm operation when the gate fails.

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

Use `workload: CronJob` plus `schedule` for periodic drift detection. Generation mode must reference a Secret containing
a dedicated `RELAY_API_KEY`; retrieval mode defaults to the chart's master-key Secret because the production ranking
diagnostics endpoint is admin-only.
