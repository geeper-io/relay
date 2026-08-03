"""Static HTML shells for the Relay admin dashboard."""

from __future__ import annotations

from html import escape

_TABLER_CSS = "https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css"


def login_page(
    *,
    error: str | None = None,
    oidc_enabled: bool = False,
    master_key_enabled: bool = True,
) -> str:
    alert = f'<div class="alert alert-danger" role="alert">{escape(error)}</div>' if error else ""
    oidc = (
        """
      <a class="btn btn-primary btn-lg w-100" href="/admin/auth/login">Sign in with company SSO</a>
    """
        if oidc_enabled
        else ""
    )
    divider = (
        '<div class="login-divider"><span>Break-glass access</span></div>'
        if oidc_enabled and master_key_enabled
        else ""
    )
    master_key = (
        """
      <form method="post" action="/admin/login" autocomplete="off">
        <label class="form-label" for="master-key">Admin master key</label>
        <input class="form-control form-control-lg" id="master-key" name="master_key" type="password"
               required autofocus autocomplete="current-password" placeholder="Enter PROXY_MASTER_KEY">
        <button class="btn btn-outline-secondary btn-lg w-100" type="submit">Use master key</button>
      </form>
    """
        if master_key_enabled
        else ""
    )
    no_method = (
        '<div class="alert alert-warning">No dashboard sign-in method is configured.</div>'
        if not oidc_enabled and not master_key_enabled
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Relay Admin</title>
  <link rel="stylesheet" href="{_TABLER_CSS}">
  <link rel="stylesheet" href="/admin/assets/admin.css">
</head>
<body class="relay-login">
  <main class="login-shell">
    <section class="login-card" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">R</div>
      <p class="eyebrow">Geeper Relay</p>
      <h1 id="login-title">Admin control plane</h1>
      <p class="text-secondary">Review MCP tool requests and make approval decisions.</p>
      {alert}
      {oidc}
      {divider}
      {master_key}
      {no_method}
      <p class="login-note">Access is exchanged for a short-lived HttpOnly session and is not stored by the page.</p>
    </section>
  </main>
</body>
</html>"""


def dashboard_page(
    *,
    csrf_token: str,
    session_expires_at: int,
    role: str,
    display_name: str | None,
    email: str | None,
) -> str:
    csrf = escape(csrf_token, quote=True)
    safe_role = escape(role, quote=True)
    identity = escape(display_name or email or "Break-glass administrator")
    identity_detail = escape(email or role.capitalize())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="relay-csrf" content="{csrf}">
  <meta name="relay-session-exp" content="{session_expires_at}">
  <meta name="relay-admin-role" content="{safe_role}">
  <title>Operations · Relay Admin</title>
  <link rel="stylesheet" href="{_TABLER_CSS}">
  <link rel="stylesheet" href="/admin/assets/admin.css">
  <script src="/admin/assets/admin.js" defer></script>
</head>
<body class="relay-admin">
  <a class="skip-link" href="#main-content">Skip to content</a>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Primary navigation">
      <div class="sidebar-brand">
        <div class="brand-mark" aria-hidden="true">R</div>
        <div><strong>Relay</strong><span>Admin</span></div>
      </div>
      <nav>
        <button class="nav-item active" data-view="overview" type="button" aria-current="page">
          <span class="nav-dot"></span>Overview
        </button>
        <button class="nav-item" data-view="users" type="button"><span class="nav-dot"></span>Users</button>
        <button class="nav-item" data-view="policies" type="button"><span class="nav-dot"></span>MCP policies</button>
        <button class="nav-item" data-view="grants" type="button"><span class="nav-dot"></span>Approval grants</button>
        <button class="nav-item" data-view="approvals" type="button"><span class="nav-dot"></span>Approvals</button>
        <span class="nav-item muted" aria-disabled="true"><span class="nav-dot"></span>Audit log <em>Soon</em></span>
      </nav>
      <div class="sidebar-footer">
        <div class="admin-identity"><strong>{identity}</strong><span>{identity_detail} · {safe_role}</span></div>
        <span class="status-line"><i></i>Relay control plane</span>
        <form method="post" action="/admin/logout">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <button class="btn btn-ghost-secondary w-100" type="submit">Sign out</button>
        </form>
      </div>
    </aside>

    <main id="main-content" class="main-content">
      <section id="view-overview" class="view-panel active" data-panel="overview">
        <header class="page-header">
          <div>
            <p class="eyebrow">Operations</p>
            <h1>Relay at a glance</h1>
            <p class="text-secondary">Usage, reliability, spend, and activity across the enterprise.</p>
          </div>
          <div class="header-actions">
            <span id="overview-updated" class="last-updated" aria-live="polite">Loading overview…</span>
            <select id="overview-range" class="form-select form-select-sm range-select" aria-label="Overview period">
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
            <button id="overview-refresh" class="btn btn-outline-secondary" type="button">Refresh</button>
          </div>
        </header>

        <div id="overview-error" class="alert alert-danger content-alert" hidden></div>
        <section class="stats-grid overview-stats" aria-label="Usage summary">
          <article class="stat-card stat-primary">
            <span>Requests</span><strong id="overview-requests">—</strong><small>Completed calls</small>
          </article>
          <article class="stat-card">
            <span>Tokens</span><strong id="overview-tokens">—</strong><small>Prompt and completion</small>
          </article>
          <article class="stat-card">
            <span>Provider cost</span><strong id="overview-cost">—</strong><small>Estimated USD</small>
          </article>
          <article class="stat-card">
            <span>Error rate</span><strong id="overview-errors">—</strong>
            <small id="overview-error-count">Across calls</small>
          </article>
          <article class="stat-card">
            <span>Avg latency</span><strong id="overview-latency">—</strong><small>End-to-end</small>
          </article>
          <article class="stat-card">
            <span>Active users</span><strong id="overview-active-users">—</strong>
            <small id="overview-user-count">In period</small>
          </article>
        </section>

        <section class="overview-grid">
          <article class="content-card activity-card">
            <div class="card-heading">
              <div><h2>Request volume</h2><p>Daily traffic in the selected period</p></div>
              <span id="overview-cache-rate" class="metric-chip">Cache —</span>
            </div>
            <div id="activity-chart" class="activity-chart" aria-label="Daily request volume chart"></div>
          </article>
          <article class="content-card signals-card">
            <div class="card-heading">
              <div><h2>Control-plane signals</h2><p>Items needing operational attention</p></div>
            </div>
            <dl class="signal-list">
              <div><dt>Pending approvals</dt><dd id="overview-pending">—</dd></div>
              <div><dt>Enabled users</dt><dd id="overview-enabled-users">—</dd></div>
              <div><dt>Teams</dt><dd id="overview-teams">—</dd></div>
              <div><dt>Cache hits</dt><dd id="overview-cache-hits">—</dd></div>
            </dl>
          </article>
        </section>

        <section class="overview-grid rankings-grid">
          <article class="content-card">
            <div class="card-heading"><div><h2>Top users</h2><p>Ranked by estimated provider cost</p></div></div>
            <div class="table-responsive">
              <table class="data-table compact-table">
                <thead><tr><th>User</th><th>Requests</th><th>Tokens</th><th>Cost</th></tr></thead>
                <tbody id="top-user-rows"></tbody>
              </table>
            </div>
          </article>
          <article class="content-card">
            <div class="card-heading"><div><h2>Top models</h2><p>Ranked by estimated provider cost</p></div></div>
            <div class="table-responsive">
              <table class="data-table compact-table">
                <thead><tr><th>Model</th><th>Requests</th><th>Tokens</th><th>Cost</th></tr></thead>
                <tbody id="top-model-rows"></tbody>
              </table>
            </div>
          </article>
        </section>
      </section>

      <section id="view-users" class="view-panel" data-panel="users" hidden>
        <header class="page-header">
          <div>
            <p class="eyebrow">Identity &amp; access</p>
            <h1>Users</h1>
            <p class="text-secondary">Inspect limits, activity, spend, and API-key posture.</p>
          </div>
          <div class="header-actions">
            <select id="users-range" class="form-select form-select-sm range-select" aria-label="User usage period">
              <option value="7">Last 7 days</option>
              <option value="30" selected>Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
            <button id="users-refresh" class="btn btn-outline-secondary" type="button">Refresh</button>
          </div>
        </header>
        <section class="content-card users-card">
          <div class="inbox-toolbar users-toolbar">
            <div>
              <h2>User directory</h2>
              <p id="users-summary" class="text-secondary">Loading users…</p>
            </div>
            <form id="user-search-form" class="search-control" role="search">
              <label class="visually-hidden" for="user-search">Search users</label>
              <input id="user-search" class="form-control" type="search" maxlength="255"
                     placeholder="Search user, team, or ID">
              <button class="btn btn-primary" type="submit">Search</button>
            </form>
          </div>
          <div id="users-loading" class="queue-state">
            <span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Loading users
          </div>
          <div id="users-error" class="queue-state queue-error" hidden></div>
          <div id="users-empty" class="queue-state empty-state" hidden>
            <div class="empty-symbol" aria-hidden="true">0</div>
            <h3>No matching users</h3>
            <p>Try a different user ID, external identity, or team.</p>
          </div>
          <div class="table-responsive">
            <table id="users-table" class="data-table" hidden>
              <thead>
                <tr>
                  <th>User</th><th>Team</th><th>Status</th><th>Limits</th><th>Usage</th><th>Cost</th><th>Keys</th>
                  <th><span class="visually-hidden">Open</span></th>
                </tr>
              </thead>
              <tbody id="user-rows"></tbody>
            </table>
          </div>
        </section>
      </section>

      <section id="view-policies" class="view-panel" data-panel="policies" hidden>
        <header class="page-header">
          <div>
            <p class="eyebrow">MCP control plane</p>
            <h1>Policies &amp; servers</h1>
            <p class="text-secondary">
              Inspect tool connectivity, simulate authorization, and activate immutable policy versions.
            </p>
          </div>
          <div class="header-actions">
            <span id="policies-updated" class="last-updated" aria-live="polite">Open to load policies</span>
            <button id="policies-refresh" class="btn btn-outline-secondary" type="button">Refresh</button>
          </div>
        </header>

        <div id="policies-error" class="alert alert-danger content-alert" hidden></div>
        <section class="policy-top-grid">
          <article class="content-card policy-active-card">
            <div class="card-heading">
              <div><h2>Active authorization</h2><p>Applied to every new MCP decision</p></div>
            </div>
            <div class="policy-active-body">
              <span id="policy-active-source" class="status-badge status-active">Loading</span>
              <strong id="policy-active-version">—</strong>
              <p id="policy-active-summary" class="text-secondary">Loading policy summary…</p>
            </div>
          </article>
          <article class="content-card server-card">
            <div class="card-heading">
              <div><h2>MCP servers</h2><p>Live discovery and tool capability checks</p></div>
            </div>
            <div id="server-inventory" class="server-inventory"><div class="mini-loading">Checking servers…</div></div>
          </article>
        </section>

        <section class="policy-workbench">
          <article class="content-card simulator-card">
            <div class="card-heading">
              <div><h2>Policy simulator</h2><p>Explain a decision without invoking the tool</p></div>
            </div>
            <form id="policy-simulator-form" class="policy-form">
              <label><span>User ID</span>
                <input id="sim-user" class="form-control" value="simulation-user" required></label>
              <label><span>Team ID <small>Optional</small></span><input id="sim-team" class="form-control"></label>
              <label class="policy-field-wide"><span>Scopes <small>Comma-separated</small></span>
                <input id="sim-scopes" class="form-control" value="mcp:*" required></label>
              <label><span>Server</span><input id="sim-server" class="form-control" placeholder="code" required></label>
              <label><span>Tool</span><input id="sim-tool" class="form-control" placeholder="execute" required></label>
              <label class="policy-field-wide"><span>Policy version</span>
                <select id="sim-version" class="form-select"><option value="">Active policy</option></select></label>
              <label class="policy-field-full"><span>Arguments <small>JSON</small></span>
                <textarea id="sim-arguments" class="form-control code-input" rows="5">{{}}</textarea></label>
              <div class="policy-form-actions policy-field-full">
                <p id="sim-error" class="decision-error" role="alert" hidden></p>
                <button id="sim-submit" class="btn btn-primary" type="submit">Simulate decision</button>
              </div>
            </form>
            <div id="simulation-result" class="simulation-result" hidden></div>
          </article>

          <article id="policy-draft-card" class="content-card draft-card" hidden>
            <div class="card-heading">
              <div><h2>Create policy draft</h2><p>Validate and save an immutable candidate</p></div>
            </div>
            <form id="policy-draft-form" class="policy-form">
              <label><span>Version</span>
                <input id="draft-version" class="form-control" placeholder="2026-08-01.1" required></label>
              <label><span>Base version</span><select id="draft-base" class="form-select"></select></label>
              <label class="policy-field-full"><span>Reason</span>
                <input id="draft-reason" class="form-control" maxlength="1000"
                       placeholder="What changes and why?" required></label>
              <label class="policy-field-full"><span>Policy document <small>JSON</small></span>
                <textarea id="draft-document" class="form-control code-input" rows="16" required></textarea></label>
              <div class="policy-form-actions policy-field-full">
                <p id="draft-error" class="decision-error" role="alert" hidden></p>
                <button id="draft-validate" class="btn btn-outline-secondary" type="button">
                  Validate &amp; preview
                </button>
                <button id="draft-save" class="btn btn-primary" type="submit">Save draft</button>
              </div>
            </form>
            <div id="draft-validation" class="validation-result" hidden></div>
          </article>
        </section>

        <section class="content-card policy-history-card">
          <div class="inbox-toolbar">
            <div>
              <h2>Policy versions</h2>
              <p id="policy-history-summary" class="text-secondary">Loading history…</p>
            </div>
          </div>
          <div id="policy-history-loading" class="queue-state">
            <span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Loading policy versions
          </div>
          <div class="table-responsive">
            <table id="policy-history-table" class="data-table" hidden>
              <thead><tr><th>Version</th><th>Status</th><th>Rules</th><th>Source</th><th>Created</th><th>Change</th><th></th></tr></thead>
              <tbody id="policy-history-rows"></tbody>
            </table>
          </div>
          <div id="activation-history" class="activation-history"></div>
        </section>
      </section>

      <section id="view-grants" class="view-panel" data-panel="grants" hidden>
        <header class="page-header">
          <div>
            <p class="eyebrow">MCP authorization</p>
            <h1>Approval grants</h1>
            <p class="text-secondary">Pre-authorize bounded tool access without waiting on every individual call.</p>
          </div>
          <div class="header-actions">
            <span id="grants-updated" class="last-updated" aria-live="polite">Open to load grants</span>
            <button id="grants-refresh" class="btn btn-outline-secondary" type="button">Refresh</button>
          </div>
        </header>

        <section id="grant-create-card" class="content-card grant-create-card" hidden>
          <div class="card-heading">
            <div><h2>Create standing grant</h2><p>Scope access by identity, tool, lifetime, and call budget.</p></div>
          </div>
          <form id="grant-create-form" class="grant-form">
            <label><span>Subject</span>
              <select id="grant-subject-type" class="form-select" required>
                <option value="user">User</option><option value="team">Team</option>
              </select>
            </label>
            <label class="grant-field-wide"><span>User or team ID</span>
              <input id="grant-subject-id" class="form-control" required maxlength="255" placeholder="Relay UUID">
            </label>
            <label><span>MCP server</span>
              <input id="grant-server" class="form-control" required maxlength="100" placeholder="code">
            </label>
            <label><span>Tool pattern</span>
              <input id="grant-tool" class="form-control" required maxlength="255" placeholder="test_*">
            </label>
            <label><span>Lifetime</span>
              <select id="grant-ttl" class="form-select">
                <option value="3600">1 hour</option><option value="28800">8 hours</option>
                <option value="86400">24 hours</option><option value="604800">7 days</option>
              </select>
            </label>
            <label><span>Maximum calls</span>
              <input id="grant-max-calls" class="form-control" type="number" min="1" max="10000" value="20" required>
            </label>
            <label class="grant-field-wide"><span>Workflow ID <small>Optional</small></span>
              <input id="grant-workflow" class="form-control" maxlength="100" placeholder="release-2026-08">
            </label>
            <label class="grant-field-full"><span>Additional argument constraints <small>JSON, optional</small></span>
              <textarea id="grant-constraints" class="form-control" rows="3"
                        placeholder='{{"allowed_values":{{"runtime":["python"]}}}}'></textarea>
            </label>
            <label class="grant-field-full"><span>Reason</span>
              <input id="grant-reason" class="form-control" maxlength="1000" required
                     placeholder="Why is this repeated access appropriate?">
            </label>
            <div class="grant-form-actions grant-field-full">
              <p id="grant-form-error" class="decision-error" role="alert" hidden></p>
              <button id="grant-create-button" class="btn btn-primary" type="submit">Create grant</button>
            </div>
          </form>
        </section>

        <section class="content-card grant-list-card">
          <div class="inbox-toolbar">
            <div><h2>Grant inventory</h2><p id="grants-summary" class="text-secondary">Loading grants…</p></div>
            <label class="form-check form-switch">
              <input id="grants-show-inactive" class="form-check-input" type="checkbox" checked>
              <span class="form-check-label">Show inactive</span>
            </label>
          </div>
          <div id="grants-loading" class="queue-state">
            <span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Loading grants
          </div>
          <div id="grants-error" class="queue-state queue-error" hidden></div>
          <div id="grants-empty" class="queue-state empty-state" hidden>
            <div class="empty-symbol" aria-hidden="true">0</div><h3>No approval grants</h3>
            <p>Protected tools continue to use one-time approval requests.</p>
          </div>
          <div class="table-responsive">
            <table id="grants-table" class="data-table" hidden>
              <thead><tr><th>Scope</th><th>Tool</th><th>Usage</th><th>Expires</th><th>Status</th><th>Reason</th><th></th></tr></thead>
              <tbody id="grant-rows"></tbody>
            </table>
          </div>
        </section>
      </section>

      <section id="view-approvals" class="view-panel" data-panel="approvals" hidden>
        <header class="page-header">
          <div>
            <p class="eyebrow">MCP governance</p>
            <h1>Approval inbox</h1>
            <p class="text-secondary">Review exactly what a model wants to run before it reaches enterprise systems.</p>
          </div>
          <div class="header-actions">
            <span id="last-updated" class="last-updated" aria-live="polite">Open to load approvals</span>
            <button id="refresh-button" class="btn btn-outline-secondary" type="button">Refresh</button>
          </div>
        </header>

        <section class="stats-grid" aria-label="Approval summary">
          <article class="stat-card stat-primary">
            <span>Needs review</span><strong id="stat-pending">—</strong><small>Pending and unexpired</small>
          </article>
          <article class="stat-card">
            <span>Approved</span><strong id="stat-approved">—</strong><small>Ready or consumed</small>
          </article>
          <article class="stat-card">
            <span>Denied</span><strong id="stat-denied">—</strong><small>Rejected requests</small>
          </article>
          <article class="stat-card">
            <span>Expiring soon</span><strong id="stat-expiring">—</strong><small>Within five minutes</small>
          </article>
        </section>

        <section class="inbox-card" aria-labelledby="queue-title">
          <div class="inbox-toolbar">
            <div>
              <h2 id="queue-title">Tool requests</h2>
              <p id="queue-summary" class="text-secondary">Fetching approval queue</p>
            </div>
            <div class="filter-tabs" role="tablist" aria-label="Approval status">
              <button class="filter-tab active" data-status="pending" role="tab" aria-selected="true">Pending</button>
              <button class="filter-tab" data-status="approved" role="tab" aria-selected="false">Approved</button>
              <button class="filter-tab" data-status="denied" role="tab" aria-selected="false">Denied</button>
              <button class="filter-tab" data-status="" role="tab" aria-selected="false">All</button>
            </div>
          </div>

          <div id="queue-loading" class="queue-state">
            <span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Loading requests
          </div>
          <div id="queue-error" class="queue-state queue-error" hidden></div>
          <div id="queue-empty" class="queue-state empty-state" hidden>
            <div class="empty-symbol" aria-hidden="true">✓</div>
            <h3>Nothing needs attention</h3>
            <p>New MCP approval requests will appear here automatically.</p>
          </div>
          <div class="table-responsive">
            <table id="approval-table" class="data-table approval-table" hidden>
              <thead>
                <tr>
                  <th>Request</th><th>Requester</th><th>Policy</th><th>Requested</th><th>Status</th>
                  <th><span class="visually-hidden">Open</span></th>
                </tr>
              </thead>
              <tbody id="approval-rows"></tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  </div>

  <div id="drawer-backdrop" class="drawer-backdrop" hidden></div>
  <aside id="approval-drawer" class="approval-drawer" aria-labelledby="drawer-title" aria-hidden="true">
    <div class="drawer-header">
      <div><p id="drawer-eyebrow" class="eyebrow">Details</p><h2 id="drawer-title">Inspect record</h2></div>
      <button id="drawer-close" class="drawer-close" type="button" aria-label="Close details">×</button>
    </div>
    <div id="drawer-content" class="drawer-content"></div>
    <div id="drawer-actions" class="drawer-actions" hidden>
      <label class="form-label" for="decision-reason">Decision reason</label>
      <textarea id="decision-reason" class="form-control" rows="3" maxlength="1000"
                placeholder="Why is this safe or unsafe?"></textarea>
      <p id="decision-error" class="decision-error" role="alert" hidden></p>
      <div class="decision-buttons">
        <button id="deny-button" class="btn btn-outline-danger" type="button">Deny request</button>
        <button id="approve-button" class="btn btn-primary" type="button">Approve once</button>
      </div>
    </div>
  </aside>

  <div id="toast" class="relay-toast" role="status" aria-live="polite" hidden></div>
</body>
</html>"""
