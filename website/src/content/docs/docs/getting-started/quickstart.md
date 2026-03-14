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

Edit `.env` and set at minimum one provider key:

```bash
# .env
OPENAI_API_KEY=sk-...          # OpenAI
ANTHROPIC_API_KEY=sk-ant-...   # Anthropic (optional)

# Auto-generated on first start if left empty:
PROXY_MASTER_KEY=
```

:::tip
Leave `PROXY_MASTER_KEY` empty on first run. The proxy generates a random 32-character key and prints it to the logs. Copy it out and set it permanently in `.env`.
:::

## 2. Start the stack

```bash
docker compose up -d
```

This starts:
- `proxy` — the Geeper Relay on port 8000
- `postgres` — PostgreSQL 16 for API keys, users, usage records
- `chromadb` — vector store for RAG (optional, controlled by `config.yaml`)

## 3. Check it's running

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

## 4. Create your first API key

Use the master key (from `.env` or from the startup logs) to create a user key:

```bash
curl -X POST http://localhost:8000/internal/api-keys \
  -H "Authorization: Bearer $PROXY_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "dev", "user_id": "alice"}'
```

Response:

```json
{
  "id": "ak_01j...",
  "name": "dev",
  "key": "llmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "llmp_xxxx",
  "user_id": "alice"
}
```

:::caution
The `key` field is shown **once**. Store it securely — it cannot be retrieved again.
:::

## 5. Make your first request

```bash
export API_KEY=llmp_xxxx...   # the key you just created

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
    api_key="llmp_xxxx...",
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
