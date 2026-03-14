---
title: POST /v1/messages
description: Anthropic Messages API compatible endpoint.
---

Native Anthropic Messages API format. Use this endpoint with the Anthropic Python SDK or Claude Code CLI by setting `ANTHROPIC_BASE_URL`.

All proxy features (PII scrubbing, RAG, rate limiting, content policy, caching) apply equally to this endpoint.

## Request

```
POST /v1/messages
x-api-key: <api-key>
anthropic-version: 2023-06-01
Content-Type: application/json
```

`Authorization: Bearer <api-key>` is also accepted as an alternative to `x-api-key`.

### Body

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | yes | Anthropic model ID (e.g. `claude-3-5-sonnet-20241022`) |
| `messages` | array | yes | Array of `{role, content}` objects |
| `max_tokens` | int | yes | Maximum output tokens |
| `system` | string | no | System prompt (prepended to context) |
| `stream` | bool | no | `false` (default). SSE streaming if `true`. |
| `tools` | array | no | Anthropic tool definitions |
| `temperature` | float | no | |
| `top_p` | float | no | |
| `top_k` | int | no | |

### Example

```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 1024,
  "system": "You are a helpful assistant.",
  "messages": [
    {"role": "user", "content": "Explain PII scrubbing in one paragraph."}
  ]
}
```

## Response (non-streaming)

```json
{
  "id": "msg_01...",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "PII scrubbing is the process of..."
    }
  ],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 38,
    "output_tokens": 92
  }
}
```

## Streaming

SSE events follow the Anthropic streaming protocol:

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_01...","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022","stop_reason":null,"usage":{"input_tokens":38,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"PII scrubbing"}}

event: message_stop
data: {"type":"message_stop"}
```

### Python (Anthropic SDK)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="https://proxy.internal",
    api_key="llmp_...",
)

# Non-streaming
message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)

# Streaming
with client.messages.stream(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Count to 5"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### Claude Code CLI

```bash
export ANTHROPIC_BASE_URL="https://proxy.internal"
export ANTHROPIC_API_KEY="llmp_..."
claude
```

Or with flags:

```bash
claude --api-url https://proxy.internal --api-key llmp_...
```
