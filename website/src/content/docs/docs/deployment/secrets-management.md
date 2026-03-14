---
title: Secrets management
description: How proxy secrets are stored, and how to bring your own from Vault, Sealed Secrets, or ESO.
---

## Default (dev)

With `secrets.create: true` (default), Helm creates a Secret from the values you pass via `--set` or a values file:

```bash
helm install llm-proxy ./helm/llm-proxy \
  --set secrets.openaiApiKey=sk-...
```

This is convenient for development but should not be used in production — `--set` values appear in shell history and Helm release history.

## Production: bring your own Secret

### Step 1 — pre-provision the Secret

Create the Secret outside of Helm (Vault Agent, External Secrets Operator, Sealed Secrets, kubectl, CI pipeline):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: llm-proxy-secrets
  namespace: default
type: Opaque
stringData:
  OPENAI_API_KEY: "sk-..."
  ANTHROPIC_API_KEY: "sk-ant-..."
  GOOGLE_CLIENT_ID: ""
  GOOGLE_CLIENT_SECRET: ""
  AUTH_BASE_URL: "https://proxy.internal"
  LANGFUSE_PUBLIC_KEY: ""
  LANGFUSE_SECRET_KEY: ""
  LANGFUSE_HOST: ""
  # DATABASE_URL: only needed when externalDatabase.url is set
```

### Step 2 — disable chart-managed Secret

```yaml
secrets:
  create: false
  existingSecret: llm-proxy-secrets
```

### Required keys

The Secret must contain all keys that the proxy references. Optional keys (e.g. `ANTHROPIC_API_KEY`) can be empty strings — they are mounted as optional env vars.

| Key | Required |
|---|---|
| `OPENAI_API_KEY` | If using OpenAI |
| `ANTHROPIC_API_KEY` | If using Anthropic directly |
| `AZURE_OPENAI_API_KEY` | If using Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | If using Azure OpenAI |
| `GOOGLE_CLIENT_ID` | If using Google SSO |
| `GOOGLE_CLIENT_SECRET` | If using Google SSO |
| `AUTH_BASE_URL` | If using Google SSO |
| `LANGFUSE_PUBLIC_KEY` | If using Langfuse |
| `LANGFUSE_SECRET_KEY` | If using Langfuse |
| `LANGFUSE_HOST` | If using self-hosted Langfuse |
| `DATABASE_URL` | If using `externalDatabase.url` |

## Master key management

The `PROXY_MASTER_KEY` is stored in a **separate** Secret (`<release>-master-key`) — it is never part of the main API keys Secret.

### Auto-generation (default)

Leave `secrets.existingMasterKeySecret` empty. The chart:
1. On first install: generates a random 32-character key and stores it in the Secret
2. On `helm upgrade`: reads the existing Secret via `lookup()` — the key is never rotated
3. On `helm uninstall`: the Secret is **kept** (`helm.sh/resource-policy: keep`)

### Bring your own master key

```yaml
secrets:
  existingMasterKeySecret: my-master-key-secret
```

The referenced Secret must contain:

```yaml
stringData:
  PROXY_MASTER_KEY: "your-32-char-key"
```

### Retrieve the auto-generated key

```bash
kubectl get secret <release>-master-key \
  -o jsonpath='{.data.PROXY_MASTER_KEY}' | base64 -d
```

## External Secrets Operator

Example `ExternalSecret` for AWS Secrets Manager:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: llm-proxy-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: SecretStore
  target:
    name: llm-proxy-secrets
    creationPolicy: Owner
  data:
    - secretKey: OPENAI_API_KEY
      remoteRef:
        key: prod/llm-proxy/openai-api-key
    - secretKey: ANTHROPIC_API_KEY
      remoteRef:
        key: prod/llm-proxy/anthropic-api-key
```

Then:

```yaml
secrets:
  create: false
  existingSecret: llm-proxy-secrets
```
