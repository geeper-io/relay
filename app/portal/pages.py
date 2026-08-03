# ruff: noqa: E501
"""Static HTML shells for Relay's self-service developer portal."""

from __future__ import annotations

from html import escape

_TABLER_CSS = "https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css"


def login_page(*, oidc_enabled: bool) -> str:
    action = (
        '<a class="btn btn-primary btn-lg w-100" href="/auth/login">Continue with company SSO</a>'
        if oidc_enabled
        else '<div class="alert alert-warning">Company SSO is not configured.</div>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Relay</title>
  <link rel="stylesheet" href="{_TABLER_CSS}">
  <link rel="stylesheet" href="/portal/assets/portal.css">
</head>
<body class="portal-login">
  <main class="login-shell">
    <section class="login-card" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">R</div>
      <p class="eyebrow">Geeper Relay</p>
      <h1 id="login-title">Developer portal</h1>
      <p class="text-secondary">See your usage, manage API keys, and connect your tools.</p>
      {action}
      <p class="login-note">Your Relay account is linked to your verified company identity.</p>
    </section>
  </main>
</body>
</html>"""


def portal_page(
    *,
    csrf_token: str,
    session_expires_at: int,
    display_name: str,
    email: str,
) -> str:
    csrf = escape(csrf_token, quote=True)
    identity = escape(display_name)
    safe_email = escape(email)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="relay-csrf" content="{csrf}">
  <meta name="relay-session-exp" content="{session_expires_at}">
  <title>Developer portal · Relay</title>
  <link rel="stylesheet" href="{_TABLER_CSS}">
  <link rel="stylesheet" href="/portal/assets/portal.css">
  <script src="/portal/assets/portal.js" defer></script>
</head>
<body class="relay-portal">
  <a class="skip-link" href="#main-content">Skip to content</a>
  <div class="portal-shell">
    <aside class="sidebar" aria-label="Portal navigation">
      <div class="sidebar-brand"><div class="brand-mark" aria-hidden="true">R</div><div><strong>Relay</strong><span>Developer</span></div></div>
      <nav>
        <button class="nav-item active" data-view="usage" type="button" aria-current="page"><span></span>Usage & limits</button>
        <button class="nav-item" data-view="keys" type="button"><span></span>API keys</button>
        <button class="nav-item" data-view="connect" type="button"><span></span>Connect</button>
      </nav>
      <div class="sidebar-footer">
        <div class="identity"><strong>{identity}</strong><span>{safe_email}</span></div>
        <form method="post" action="/portal/logout">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <button class="btn btn-ghost-secondary w-100" type="submit">Sign out</button>
        </form>
      </div>
    </aside>

    <main id="main-content" class="main-content">
      <div id="global-error" class="alert alert-danger" hidden></div>

      <section class="view-panel active" data-panel="usage">
        <header class="page-header">
          <div><p class="eyebrow">Your workspace</p><h1>Usage & limits</h1><p class="text-secondary">Current capacity and recent Relay activity.</p></div>
          <select id="usage-range" class="form-select" aria-label="Usage period"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select>
        </header>
        <section class="stats-grid" aria-label="Usage summary">
          <article class="stat-card accent"><span>Requests</span><strong id="stat-requests">—</strong><small id="stat-errors">Recent activity</small></article>
          <article class="stat-card"><span>Tokens</span><strong id="stat-tokens">—</strong><small id="stat-cost">Estimated cost</small></article>
          <article class="stat-card"><span>Daily budget</span><strong id="stat-daily">—</strong><div class="meter"><i id="daily-meter"></i></div><small id="stat-daily-detail">Today</small></article>
          <article class="stat-card"><span>Active keys</span><strong id="stat-keys">—</strong><small id="stat-keys-detail">Self-service allowance</small></article>
        </section>
        <section class="content-grid">
          <article class="panel wide"><div class="panel-heading"><div><h2>Daily token usage</h2><p>Tokens processed through your keys</p></div></div><div id="usage-chart" class="bar-chart" aria-label="Daily token usage chart"></div></article>
          <article class="panel"><div class="panel-heading"><div><h2>Effective limits</h2><p>User and team controls</p></div></div><dl id="limits-list" class="limit-list"></dl></article>
          <article class="panel"><div class="panel-heading"><div><h2>Models</h2><p>Usage in this period</p></div></div><div id="models-list" class="model-list"></div></article>
        </section>
      </section>

      <section class="view-panel" data-panel="keys" hidden>
        <header class="page-header"><div><p class="eyebrow">Credentials</p><h1>API keys</h1><p class="text-secondary">Create scoped keys for each device or workload. Secrets are shown once.</p></div><button id="new-key-button" class="btn btn-primary" type="button">Create API key</button></header>
        <div class="security-note"><strong>Keep keys separate.</strong> Use one key per application so you can rotate or revoke it without interrupting other work.</div>
        <section class="panel"><div class="table-wrap"><table class="key-table"><thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Last used</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody id="keys-body"></tbody></table></div><div id="keys-empty" class="empty-state" hidden><strong>No API keys yet</strong><span>Create a key to connect your first client.</span></div></section>
      </section>

      <section class="view-panel" data-panel="connect" hidden>
        <header class="page-header"><div><p class="eyebrow">Quickstart</p><h1>Connect to Relay</h1><p class="text-secondary">Choose a client and copy a working configuration.</p></div></header>
        <div class="connect-layout">
          <nav class="guide-tabs" aria-label="Integration guides">
            <button class="guide-tab active" data-guide="openai" type="button">OpenAI SDK</button>
            <button class="guide-tab" data-guide="anthropic" type="button">Anthropic SDK</button>
            <button class="guide-tab" data-guide="claude" type="button">Claude Code</button>
            <button class="guide-tab" data-guide="mcp" type="button">MCP clients</button>
            <button class="guide-tab" data-guide="responses" type="button">Responses + tools</button>
          </nav>
          <div class="guide-content">
            <article class="guide active" data-guide-panel="openai"><p class="guide-kicker">OpenAI-compatible</p><h2>Use the OpenAI Python SDK</h2><p>Point the SDK at Relay and use any active key with the <code>chat</code> or <code>responses</code> scope.</p><pre id="snippet-openai"></pre><button class="copy-code" data-copy="snippet-openai" type="button">Copy code</button></article>
            <article class="guide" data-guide-panel="anthropic" hidden><p class="guide-kicker">Anthropic-compatible</p><h2>Use the Anthropic Python SDK</h2><p>Relay accepts the Messages API while retaining enterprise policy and usage attribution.</p><pre id="snippet-anthropic"></pre><button class="copy-code" data-copy="snippet-anthropic" type="button">Copy code</button></article>
            <article class="guide" data-guide-panel="claude" hidden><p class="guide-kicker">Agent harness</p><h2>Connect Claude Code</h2><p>Set the Relay base URL and authenticate with a scoped key before starting the harness.</p><pre id="snippet-claude"></pre><button class="copy-code" data-copy="snippet-claude" type="button">Copy commands</button></article>
            <article class="guide" data-guide-panel="mcp" hidden><p class="guide-kicker">Remote MCP</p><h2>Connect Relay as an MCP server</h2><p>Your key needs an <code>mcp:*</code> or narrower MCP scope. Relay discovers only the tools that identity may use.</p><pre id="snippet-mcp"></pre><button class="copy-code" data-copy="snippet-mcp" type="button">Copy configuration</button></article>
            <article class="guide" data-guide-panel="responses" hidden><p class="guide-kicker">Agentic workflows</p><h2>Use Responses with Relay MCP</h2><p>Relay can delegate a short-lived credential to the model provider while approvals and policy stay inside Relay.</p><pre id="snippet-responses"></pre><button class="copy-code" data-copy="snippet-responses" type="button">Copy request</button></article>
          </div>
        </div>
      </section>
    </main>
  </div>

  <dialog id="key-dialog" class="portal-dialog">
    <form id="key-form" method="dialog">
      <div class="dialog-heading"><div><p class="eyebrow">New credential</p><h2>Create API key</h2></div><button class="icon-button" value="cancel" aria-label="Close" type="submit">×</button></div>
      <label class="form-label" for="key-name">Name</label><input id="key-name" class="form-control" maxlength="80" required placeholder="dev-laptop" autocomplete="off">
      <label class="form-label">Scopes</label><div id="scope-options" class="scope-options"></div>
      <label class="form-label" for="key-ttl">Expires</label><select id="key-ttl" class="form-select"><option value="30">In 30 days</option><option value="90" selected>In 90 days</option><option value="365">In 1 year</option></select>
      <div id="key-form-error" class="alert alert-danger" hidden></div>
      <div class="dialog-actions"><button class="btn btn-ghost-secondary" value="cancel" type="submit">Cancel</button><button id="create-key-submit" class="btn btn-primary" type="button">Create key</button></div>
    </form>
  </dialog>

  <dialog id="secret-dialog" class="portal-dialog secret-dialog">
    <div class="dialog-heading"><div><p class="eyebrow">Shown once</p><h2>Save your API key</h2></div></div>
    <p>This secret cannot be retrieved again. Store it in your password manager or secret store now.</p>
    <div class="secret-row"><code id="new-key-secret"></code><button id="copy-secret" class="btn btn-primary" type="button">Copy</button></div>
    <div class="dialog-actions"><button id="secret-done" class="btn btn-outline-secondary" type="button">I saved it</button></div>
  </dialog>
</body>
</html>"""
