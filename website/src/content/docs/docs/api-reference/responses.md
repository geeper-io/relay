---
title: POST /v1/responses
description: OpenAI Responses API compatibility, typed items, tools, streaming, and privacy defaults.
---

Relay exposes the OpenAI Responses API at `POST /v1/responses`. It uses LiteLLM's native Responses adapter, preserving
typed input/output items, function calls, structured outputs, reasoning settings, and provider-hosted tools where the
selected deployment supports them. See OpenAI's [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses).

The API key requires the `responses` scope.

```bash
curl http://localhost:8000/v1/responses \
  -H "Authorization: Bearer $RELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "general",
    "input": "Summarize our incident response process.",
    "store": false
  }'
```

The result is an OpenAI-compatible `response` object with a typed `output` array. Relay applies content policy, token
budgets, PII scrubbing/restoration, ACL-aware RAG, deployment routing, usage accounting, audit logging, and telemetry.

## Streaming

Set `stream: true` to receive typed server-sent events such as `response.created`, `response.output_text.delta`, and
`response.completed`. Relay preserves both the SSE `event` name and the JSON event `type`.

## Privacy and state

Relay defaults `store` to `false`, unlike the upstream Responses API default. Set `responses.default_store: true` or
pass `store: true` explicitly when provider-side response state is approved. `previous_response_id` and `store: true`
request the `stateful` routing capability and can therefore be denied by policy.

## Capability routing

Relay infers these capabilities from the request: `responses`, `streaming`, `vision`, `reasoning`, `structured_outputs`,
`stateful`, `tools`, and specific hosted tools such as `tool:web_search`. A deployment missing a required capability is
rejected before an upstream call.

Every successful response includes `X-Relay-Deployment` and `X-Relay-Policy-Version` headers.
