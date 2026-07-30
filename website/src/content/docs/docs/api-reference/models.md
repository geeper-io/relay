---
title: GET /v1/models
description: List available models in OpenAI format.
---

Returns the list of models the proxy is configured to allow, in OpenAI's model list format.

## Request

```
GET /v1/models
Authorization: Bearer <api-key>
```

## Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o",
      "object": "model",
      "created": 1710000000,
      "owned_by": "openai"
    },
    {
      "id": "gpt-4o-mini",
      "object": "model",
      "created": 1710000000,
      "owned_by": "openai"
    },
    {
      "id": "claude-3-5-sonnet-20241022",
      "object": "model",
      "created": 1710000000,
      "owned_by": "anthropic"
    }
  ]
}
```

The list combines `llm.deployments` aliases with canonical models from `llm.allowed_models`. Deployment aliases are
marked `owned_by: "relay"`; canonical models remain `owned_by: "proxy"`.

## Model aliases

If you've configured aliases in `config.yaml`:

```yaml
llm:
  model_aliases:
    gpt-4: gpt-4o
    claude: claude-3-5-sonnet-20241022
```

Requests using the legacy alias (`gpt-4`) are silently rewritten to the target (`gpt-4o`) before routing. Legacy
aliases do not appear in `GET /v1/models`.

Legacy `model_aliases` rewrites remain hidden. Prefer declared `deployments` for new configurations because they are
visible to clients and carry capability/fallback metadata enforced by versioned routing policies.
