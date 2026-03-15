---
title: Embeddings
description: OpenAI-compatible /v1/embeddings endpoint for generating text embeddings.
---

Generate vector embeddings from text. The endpoint is OpenAI-compatible and works with any client that targets the OpenAI Embeddings API.

## Endpoint

```
POST /v1/embeddings
```

## Request

```json
{
  "model": "text-embedding-3-small",
  "input": "The quick brown fox jumps over the lazy dog"
}
```

`input` also accepts an array of strings for batch embedding:

```json
{
  "model": "text-embedding-3-small",
  "input": ["first sentence", "second sentence"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | No* | Embedding model to use. Defaults to `llm.default_embedding_model` if configured |
| `input` | string or string[] | Yes | Text to embed |

*Required if `llm.default_embedding_model` is not set in config.

## Response

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.0023064255, -0.009327292, ...]
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 9,
    "total_tokens": 9
  }
}
```

## Authentication

Both Relay-issued keys (`gr-...`) and passthrough keys work:

```bash
# Relay-issued key — uses server-configured OpenAI/Anthropic credentials
curl https://relay.company.com/v1/embeddings \
  -H "Authorization: Bearer gr-..." \
  -H "Content-Type: application/json" \
  -d '{"model": "text-embedding-3-small", "input": "Hello world"}'

# Passthrough — your own OpenAI key, routed through Relay middleware
curl https://relay.company.com/v1/embeddings \
  -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{"model": "text-embedding-3-small", "input": "Hello world"}'
```

## SDK usage

```python
from openai import OpenAI

client = OpenAI(
    api_key="gr-...",
    base_url="https://relay.company.com/v1",
)

response = client.embeddings.create(
    model="text-embedding-3-small",
    input="Hello world",
)
print(response.data[0].embedding)
```

## Supported models

Any embedding model supported by LiteLLM works — the model name is passed through directly. Common options:

| Provider | Model |
|---|---|
| OpenAI | `text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002` |
| Anthropic | Not supported (no embedding API) |
| Cohere | `embed-english-v3.0`, `embed-multilingual-v3.0` |
| Azure OpenAI | `azure/text-embedding-3-small` |

## Configuration

Set a default embedding model so clients don't need to specify it every request:

```yaml
llm:
  default_embedding_model: text-embedding-3-small
```

Helm: `config.llm.defaultEmbeddingModel`
