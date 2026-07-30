---
title: Deployment and policy routing
description: Stable deployment aliases, capability checks, team overrides, and versioned routing decisions.
---

Deployment aliases decouple client-facing model names from provider model IDs. Define the provider model, supported
capabilities, and deployment-specific fallbacks once:

```yaml
llm:
  default_model: general
  allowed_models: [openai/gpt-4o, anthropic/claude-sonnet-4-6]
  deployments:
    general:
      model: openai/gpt-4o
      capabilities: [chat, responses, streaming, tools, structured_outputs]
      fallback_models: [anthropic/claude-sonnet-4-6]
    vision:
      model: openai/gpt-4o
      capabilities: [chat, responses, streaming, vision]
```

Clients send `model: general` or `model: vision`. `GET /v1/models` exposes deployment aliases as Relay-owned models.

## Versioned policies

```yaml
routing:
  active_policy_version: "2026-07-30"
  require_declared_capabilities: true
  policies:
    "2026-07-30":
      default_deployment: general
      allowed_deployments: [general, vision]
      allowed_capabilities: [chat, responses, embeddings, streaming, tools, vision, structured_outputs]
      denied_capabilities: [tool:computer_use]
      capability_routes:
        vision: vision
      team_overrides:
        team-restricted-uuid:
          allowed_deployments: [general]
```

Use `model: auto` to select `capability_routes`, then the default deployment or first compatible deployment. Direct
model IDs remain backward compatible unless `require_declared_capabilities` is enabled.

The selected version, requested model, physical model, deployment, and inferred capabilities are recorded in usage
audit metadata, Langfuse metadata, OpenTelemetry spans, Prometheus routing metrics, and response headers.
