---
title: MCP gateway and approvals
description: Broker remote MCP tools through Relay authorization, human approval, auditing, and output controls.
---

Relay can act as an MCP server at `POST /mcp` while brokering tools from configured remote Streamable HTTP servers.
Relay does not start local processes or execute tool code. Code execution, browsers, CI jobs, and infrastructure actions
remain separate MCP products behind the gateway.

The implementation follows the stable MCP `2025-11-25` lifecycle: initialization, capability negotiation,
`tools/list`, JSON Schema 2020-12 validation, and `tools/call`. Remote tools are exposed as `<server>__<tool>` to avoid
name collisions.

## Configuration

```yaml
mcp:
  enabled: true
  protocol_version: "2025-11-25"
  active_policy_version: "2026-07-30"
  servers:
    code:
      url: "https://code-tools.internal/mcp"
      transport: streamable_http
      description: "External sandbox service"
      headers_env:
        Authorization: CODE_MCP_AUTHORIZATION
  policies:
    "2026-07-30":
      default_action: deny
      rules:
        - name: safe-tests
          server: code
          tool: "test_*"
          action: allow
        - name: code-execution
          server: code
          tool: execute
          action: require_approval
          constraints:
            allowed_values:
              runtime: [python, node]
            denied_patterns:
              command: ["rm\\s+-rf", "sudo"]
```

Server credentials are read only from environment variables named by `headers_env`; they are never returned through
the registry API. With Helm, place those environment variables in a Secret and set
`secrets.mcpCredentialSecret` to its name. HTTPS is mandatory unless `allow_insecure_http` is explicitly enabled.
Redirects are never followed, preventing credentials from being forwarded to another origin.

## Tool authorization

Relay API keys need `mcp:*`, `mcp:<server>:*`, or `mcp:<server>:<tool>`. Versioned policy rules are evaluated in order
and may `allow`, `deny`, or `require_approval`. Rules can target users or teams, require additional scopes, constrain
allowed argument values, require fields, reject argument patterns, and limit string lengths. The default action should
remain `deny`.

Passthrough provider keys cannot access MCP. Tool arguments are validated against the server-published JSON Schema
immediately before execution.

## Human approval flow

When a tool requires approval, `tools/call` returns an error result containing a durable approval ID. An administrator
reviews the exact arguments through:

```text
GET  /internal/mcp/approvals
POST /internal/mcp/approvals/{id}/decision
```

The MCP client calls the built-in `relay_approval_status` tool. After approval it receives a short-lived signed token,
then retries the original tool with `_relay_approval_token`. Tokens are bound to the user, server, tool, canonical
argument hash, policy version, and expiry. They are consumed before execution and cannot be replayed. Changing any
argument requires a new approval.

Use `_relay_purpose` on the original tool call to give the approver a human-readable explanation.

## Security and operations

Tool outputs are recursively PII-scrubbed and rejected when they exceed `max_result_bytes`. Timeouts, authorization
decisions, approval events, executions, failures, latency, policy versions, and PII counts are written to the audit log
and exposed through Prometheus/OpenTelemetry.

Approval records contain tool arguments so approvers can review the exact action. Treat the database and admin API as
sensitive systems and avoid placing raw secrets in tool arguments.

The REST endpoints under `/v1/mcp` provide the same registry and invocation functions for internal UIs and automation.
