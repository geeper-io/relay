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
          grant:
            subject: user
            ttl_seconds: 28800
            max_calls: 20
            constraints:
              allowed_values:
                runtime: [python]
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

## Policy control plane

Configured versions bootstrap Relay. The admin dashboard can then validate and save immutable database drafts,
compare them with the active document, and simulate exact caller and argument contexts through the production policy
engine. Activation atomically updates a shared database pointer, so all replicas converge without a configuration
rollout. Rollback reactivates an earlier immutable version and records the actor, reason, and transition. Approvals and
standing grants from another version never match the new policy.

The same view probes configured servers independently and shows health, discovery latency, remote tools, and their
published input schemas without exposing server credentials.

## Human approval flow

When a tool requires approval, `tools/call` returns an error result containing a durable approval ID. An administrator
reviews the exact arguments through:

```text
GET  /internal/mcp/approvals
POST /internal/mcp/approvals/{id}/decision
```

For interactive review, enable the browser dashboard and open `/admin`. Its approval inbox shows the requester,
purpose, policy version, expiry, and exact arguments, and requires a reason for every approve or deny decision.

The MCP client calls the built-in `relay_approval_status` tool. After approval it receives a short-lived signed token,
then retries the original tool with `_relay_approval_token`. Tokens are bound to the user, server, tool, canonical
argument hash, policy version, and expiry. They are consumed before execution and cannot be replayed. Changing any
argument requires a new approval.

Use `_relay_purpose` on the original tool call to give the approver a human-readable explanation.

## Standing approval grants

Repeated reviewed workflows do not need an administrator for every call. Relay can create a durable standing grant
after the first approval, or an admin can create one directly in the dashboard. Each grant is bounded by user or team,
exact server, glob-style tool pattern, active policy version, expiry, maximum calls, and optional argument constraints.

The policy example above offers an eight-hour, 20-call user grant when the first `execute` call is approved. The
approval UI shows that offer before the decision. An unconstrained matching grant makes a tool automatic at discovery;
a constrained grant is checked and consumed once exact arguments arrive. In both cases, current key scopes and policy
rules still apply, and `deny` always wins.

Relay reserves a call before remote execution, so failed calls consume budget. Creation, consumption, and revocation
are audited, and admins can revoke active access immediately from the grant inventory or API.

## Security and operations

Tool outputs are recursively PII-scrubbed and rejected when they exceed `max_result_bytes`. Timeouts, authorization
decisions, approval events, executions, failures, latency, policy versions, and PII counts are written to the audit log
and exposed through Prometheus/OpenTelemetry.

Approval records contain tool arguments so approvers can review the exact action. Treat the database and admin API as
sensitive systems and avoid placing raw secrets in tool arguments.

The REST endpoints under `/v1/mcp` provide the same registry and invocation functions for internal UIs and automation.
