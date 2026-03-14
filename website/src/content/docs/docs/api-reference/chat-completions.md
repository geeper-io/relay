---
title: POST /v1/chat/completions
description: OpenAI-compatible chat completions endpoint.
---

Full OpenAI Chat Completions API compatibility. Any client or SDK built for OpenAI works with zero code changes — only the `base_url` needs updating.

## Request

```
POST /v1/chat/completions
Authorization: Bearer <api-key>
Content-Type: application/json
```

### Body

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | yes | Model ID. Must be in `allowedModels` or a defined alias. |
| `messages` | array | yes | Array of `{role, content}` objects |
| `stream` | bool | no | `false` (default). Set `true` for SSE streaming. |
| `temperature` | float | no | Sampling temperature (0–2) |
| `max_tokens` | int | no | Maximum output tokens. Capped by `perModelMaxTokens` if set. |
| `tools` | array | no | OpenAI tool/function definitions |
| `tool_choice` | string/object | no | `auto`, `none`, or specific tool |
| `top_p` | float | no | Nucleus sampling |
| `frequency_penalty` | float | no | |
| `presence_penalty` | float | no | |
| `user` | string | no | Passed through to provider |

### Example

```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is RAG?"}
  ],
  "max_tokens": 512
}
```

## Response (non-streaming)

Standard OpenAI `ChatCompletion` object:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "RAG stands for Retrieval-Augmented Generation..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 87,
    "total_tokens": 129
  }
}
```

## Streaming

Set `stream: true` to receive Server-Sent Events:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"content":"RAG"},"index":0}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"content":" stands"},"index":0}]}

data: [DONE]
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://proxy.internal/v1",
    api_key="llmp_...",
)

# Non-streaming
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)

# Streaming
for chunk in client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Count to 5"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")
```

## Model routing

The `model` field is processed as follows:

1. Check `model_aliases` — rewrite if a match is found
2. Validate against `allowed_models` — return 400 if not allowed
3. Check `per_model_max_tokens` — cap `max_tokens` if set
4. Route to the appropriate provider via LiteLLM

To route an Anthropic model through this endpoint:

```json
{"model": "claude-3-5-sonnet-20241022", ...}
```

LiteLLM detects the provider from the model name prefix automatically.
