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
dataset, and output report. JSONL cases support `equals`, `contains`, `regex`, and `json_keys` graders:

```json
{"id":"capital","input":"Capital of Finland? City only.","expected":{"equals":"Helsinki"}}
```

The JSON report contains every output and error plus pass rate, mean score, average/p95 latency, token totals, actual
routed deployment, and policy version for each requested deployment. The command exits non-zero if any case fails, so
it can gate policy/model changes in CI.

Evaluation reports contain model outputs and should be handled as potentially sensitive artifacts.
