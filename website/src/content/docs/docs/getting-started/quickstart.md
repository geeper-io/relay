---
title: Quickstart (Docker Compose)
description: Run Geeper Relay locally in under 5 minutes using Docker Compose.
---

## Prerequisites

- Docker ≥ 24 and Docker Compose v2
- An API key from at least one LLM provider (OpenAI, Anthropic, or Azure OpenAI)

## 1. Clone and configure

```bash
git clone https://github.com/geeper-io/relay
cd relay
cp .env.example .env
```

Edit `.env`, set at least one provider key, and generate a master key:

```bash
# .env
OPENAI_API_KEY=sk-...          # OpenAI
ANTHROPIC_API_KEY=sk-ant-...   # Anthropic (optional)

# Generate with: openssl rand -hex 32
PROXY_MASTER_KEY=<paste-generated-value>
```

:::caution
Docker Compose requires `PROXY_MASTER_KEY`. Startup rejects missing and known placeholder values. Helm installations
still auto-generate and preserve this key.
:::

## 2. Start the stack

```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts:
- `proxy` — the Geeper Relay on port 8000
- `postgres` — PostgreSQL 16 for API keys, users, usage records
- embedded ChromaDB storage — vector store for RAG (optional, controlled by `config.yaml`)

## 3. Check it's running

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

## 4. Create your first API key

Create a user, then issue a scoped key. These admin endpoints accept query parameters:

```bash
USER_ID=$(curl -s -X POST \
  'http://localhost:8000/internal/users?external_id=alice%40example.com' \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" | jq -r .id)

curl -X POST \
  "http://localhost:8000/internal/api-keys?user_id=$USER_ID&name=dev&scopes=chat" \
  -H "Authorization: Bearer $PROXY_MASTER_KEY"
```

Response:

```json
{
  "id": "<uuid>",
  "key": "gr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "gr-xxxxxxxxx",
  "scopes": ["chat"],
  "expires_at": null
}
```

:::caution
The `key` field is shown **once**. Store it securely — it cannot be retrieved again.
:::

## 5. Make your first request

```bash
export API_KEY=gr-xxxx...   # the key you just created

curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello from Geeper Relay!"}]
  }'
```

Or with the OpenAI Python SDK — zero code changes:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="gr-xxxx...",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## Next steps

- [Configuration reference](/docs/getting-started/configuration) — tune rate limits, PII, content policy
- [First API key](/docs/getting-started/first-api-key) — admin API and Google SSO setup
- [Kubernetes deployment](/docs/getting-started/kubernetes) — production Helm chart
