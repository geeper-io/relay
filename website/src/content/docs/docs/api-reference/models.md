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

The list is derived from `config.yaml` → `llm.allowed_models`. Model aliases are **not** included — only the canonical names.

## Model aliases

If you've configured aliases in `config.yaml`:

```yaml
llm:
  model_aliases:
    gpt-4: gpt-4o
    claude: claude-3-5-sonnet-20241022
```

Requests using the alias (`gpt-4`) are silently rewritten to the target (`gpt-4o`) before routing. Aliases do not appear in `GET /v1/models`.
