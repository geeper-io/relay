(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="relay-csrf"]')?.content || "";
  const adminRole = document.querySelector('meta[name="relay-admin-role"]')?.content || "viewer";
  const state = {
    view: "overview",
    overviewLoaded: false,
    usersLoaded: false,
    policiesLoaded: false,
    grantsLoaded: false,
    approvalsLoaded: false,
    users: [],
    policies: [],
    activePolicy: null,
    grants: [],
    approvals: [],
    approvalStatus: "pending",
    selectedApprovalId: null,
    lastFocus: null,
  };

  const byId = (id) => document.getElementById(id);
  const elements = {
    drawer: byId("approval-drawer"),
    backdrop: byId("drawer-backdrop"),
    drawerContent: byId("drawer-content"),
    drawerActions: byId("drawer-actions"),
    drawerClose: byId("drawer-close"),
    drawerTitle: byId("drawer-title"),
    drawerEyebrow: byId("drawer-eyebrow"),
    reason: byId("decision-reason"),
    decisionError: byId("decision-error"),
    approve: byId("approve-button"),
    deny: byId("deny-button"),
    toast: byId("toast"),
    approvalLoading: byId("queue-loading"),
    approvalError: byId("queue-error"),
    approvalEmpty: byId("queue-empty"),
    approvalTable: byId("approval-table"),
    approvalRows: byId("approval-rows"),
    approvalSummary: byId("queue-summary"),
    approvalUpdated: byId("last-updated"),
    approvalRefresh: byId("refresh-button"),
    usersLoading: byId("users-loading"),
    usersError: byId("users-error"),
    usersEmpty: byId("users-empty"),
    usersTable: byId("users-table"),
    userRows: byId("user-rows"),
    usersSummary: byId("users-summary"),
    usersRefresh: byId("users-refresh"),
    usersRange: byId("users-range"),
    userSearch: byId("user-search"),
    userSearchForm: byId("user-search-form"),
    overviewRefresh: byId("overview-refresh"),
    overviewRange: byId("overview-range"),
    overviewUpdated: byId("overview-updated"),
    overviewError: byId("overview-error"),
    grantsUpdated: byId("grants-updated"),
    grantsRefresh: byId("grants-refresh"),
    grantsShowInactive: byId("grants-show-inactive"),
    grantsLoading: byId("grants-loading"),
    grantsError: byId("grants-error"),
    grantsEmpty: byId("grants-empty"),
    grantsTable: byId("grants-table"),
    grantRows: byId("grant-rows"),
    grantsSummary: byId("grants-summary"),
    grantCreateCard: byId("grant-create-card"),
    grantCreateForm: byId("grant-create-form"),
    grantFormError: byId("grant-form-error"),
    grantCreateButton: byId("grant-create-button"),
    grantSubjectType: byId("grant-subject-type"),
    grantSubjectId: byId("grant-subject-id"),
    grantServer: byId("grant-server"),
    grantTool: byId("grant-tool"),
    grantTtl: byId("grant-ttl"),
    grantMaxCalls: byId("grant-max-calls"),
    grantWorkflow: byId("grant-workflow"),
    grantConstraints: byId("grant-constraints"),
    grantReason: byId("grant-reason"),
    policiesUpdated: byId("policies-updated"),
    policiesRefresh: byId("policies-refresh"),
    policiesError: byId("policies-error"),
    policyActiveSource: byId("policy-active-source"),
    policyActiveVersion: byId("policy-active-version"),
    policyActiveSummary: byId("policy-active-summary"),
    serverInventory: byId("server-inventory"),
    policyHistoryLoading: byId("policy-history-loading"),
    policyHistoryTable: byId("policy-history-table"),
    policyHistoryRows: byId("policy-history-rows"),
    policyHistorySummary: byId("policy-history-summary"),
    activationHistory: byId("activation-history"),
    simulatorForm: byId("policy-simulator-form"),
    simUser: byId("sim-user"),
    simTeam: byId("sim-team"),
    simScopes: byId("sim-scopes"),
    simServer: byId("sim-server"),
    simTool: byId("sim-tool"),
    simVersion: byId("sim-version"),
    simArguments: byId("sim-arguments"),
    simError: byId("sim-error"),
    simSubmit: byId("sim-submit"),
    simulationResult: byId("simulation-result"),
    policyDraftCard: byId("policy-draft-card"),
    policyDraftForm: byId("policy-draft-form"),
    draftVersion: byId("draft-version"),
    draftBase: byId("draft-base"),
    draftReason: byId("draft-reason"),
    draftDocument: byId("draft-document"),
    draftError: byId("draft-error"),
    draftValidate: byId("draft-validate"),
    draftSave: byId("draft-save"),
    draftValidation: byId("draft-validation"),
  };

  const shortId = (value) => value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
  const dateValue = (value) => value ? new Date(value) : null;
  const number = (value) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
  const integer = (value) => new Intl.NumberFormat().format(value || 0);
  const money = (value) => new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value >= 100 ? 0 : 2,
    maximumFractionDigits: value >= 1 ? 2 : 4,
  }).format(value || 0);
  const percent = (value) => `${((value || 0) * 100).toFixed(value >= 0.1 ? 1 : 2)}%`;

  function relativeTime(value) {
    const date = dateValue(value);
    if (!date || Number.isNaN(date.valueOf())) return "Never";
    const seconds = Math.round((date.valueOf() - Date.now()) / 1000);
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
    const minutes = Math.round(seconds / 60);
    if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
    const hours = Math.round(minutes / 60);
    if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
    return formatter.format(Math.round(hours / 24), "day");
  }

  function exactTime(value) {
    const date = dateValue(value);
    return date && !Number.isNaN(date.valueOf()) ? date.toLocaleString() : "Never";
  }

  function setVisible(element, visible) {
    if (element) element.hidden = !visible;
  }

  function appendText(parent, tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value;
    parent.appendChild(element);
    return element;
  }

  function statusBadge(status) {
    const badge = document.createElement("span");
    badge.className = `status-badge status-${status}`;
    badge.textContent = status === "consumed" ? "Consumed" : `${status.charAt(0).toUpperCase()}${status.slice(1)}`;
    return badge;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (response.status === 401) {
      window.location.assign("/admin/login");
      throw new Error("Session expired");
    }
    const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload?.detail || `Relay returned ${response.status}`);
    return payload;
  }

  function switchView(view) {
    state.view = view;
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      const active = panel.dataset.panel === view;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (view === "overview" && !state.overviewLoaded) loadOverview();
    if (view === "users" && !state.usersLoaded) loadUsers();
    if (view === "policies" && !state.policiesLoaded) loadPolicies();
    if (view === "grants" && !state.grantsLoaded) loadGrants();
    if (view === "approvals" && !state.approvalsLoaded) loadApprovals();
  }

  function renderActivityChart(rows) {
    const chart = byId("activity-chart");
    chart.replaceChildren();
    if (!rows.length) {
      appendText(chart, "p", "chart-empty", "No requests in this period.");
      return;
    }
    const max = Math.max(...rows.map((row) => row.requests), 1);
    rows.forEach((row) => {
      const column = document.createElement("div");
      column.className = "chart-column";
      column.title = `${row.period}: ${integer(row.requests)} requests, ${money(row.cost_usd)}`;
      const bar = document.createElement("span");
      bar.className = "chart-bar";
      bar.style.height = `${Math.max(4, Math.round((row.requests / max) * 100))}%`;
      column.appendChild(bar);
      if (rows.length <= 14 || row === rows.at(-1)) {
        appendText(column, "small", "", new Date(`${row.period}T00:00:00`).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }));
      }
      chart.appendChild(column);
    });
  }

  function renderRankingRows(target, rows, dimension) {
    target.replaceChildren();
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = appendText(row, "td", "empty-cell", "No usage in this period");
      cell.colSpan = 4;
      target.appendChild(row);
      return;
    }
    rows.forEach((item) => {
      const row = document.createElement("tr");
      const label = document.createElement("td");
      if (dimension === "user") {
        const button = appendText(label, "button", "table-link", item.external_id || shortId(item.user_id));
        button.type = "button";
        button.addEventListener("click", () => openUser(item.user_id, button));
        appendText(label, "span", "cell-subtle", item.team_name || shortId(item.user_id));
      } else {
        appendText(label, "span", "request-name", item.model || "Unknown model");
      }
      row.appendChild(label);
      appendText(row, "td", "", integer(item.requests));
      appendText(row, "td", "", number(item.total_tokens));
      appendText(row, "td", "money-cell", money(item.cost_usd));
      target.appendChild(row);
    });
  }

  async function loadOverview() {
    elements.overviewRefresh.disabled = true;
    elements.overviewError.hidden = true;
    try {
      const payload = await api(`/admin/api/overview?days=${elements.overviewRange.value}`);
      const totals = payload.totals;
      byId("overview-requests").textContent = number(totals.requests);
      byId("overview-tokens").textContent = number(totals.total_tokens);
      byId("overview-cost").textContent = money(totals.cost_usd);
      byId("overview-errors").textContent = percent(totals.error_rate);
      byId("overview-error-count").textContent = `${integer(totals.errors)} failed calls`;
      byId("overview-latency").textContent = `${integer(Math.round(totals.avg_latency_ms))} ms`;
      byId("overview-active-users").textContent = integer(payload.users.active_in_window);
      byId("overview-user-count").textContent = `${integer(payload.users.total)} total users`;
      byId("overview-pending").textContent = integer(payload.pending_approvals);
      byId("overview-enabled-users").textContent = `${integer(payload.users.enabled)} / ${integer(payload.users.total)}`;
      byId("overview-teams").textContent = integer(payload.teams);
      byId("overview-cache-hits").textContent = integer(totals.cache_hits);
      byId("overview-cache-rate").textContent = `Cache ${percent(totals.cache_hit_rate)}`;
      renderActivityChart(payload.daily || []);
      renderRankingRows(byId("top-user-rows"), payload.top_users || [], "user");
      renderRankingRows(byId("top-model-rows"), payload.top_models || [], "model");
      elements.overviewUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })}`;
      state.overviewLoaded = true;
    } catch (error) {
      elements.overviewError.textContent = `Could not load the operations overview. ${error.message}`;
      elements.overviewError.hidden = false;
      elements.overviewUpdated.textContent = "Refresh failed";
    } finally {
      elements.overviewRefresh.disabled = false;
    }
  }

  function renderUsers(payload) {
    state.users = payload.items || [];
    elements.userRows.replaceChildren();
    elements.usersSummary.textContent = `${integer(payload.total)} ${payload.total === 1 ? "user" : "users"} · ${payload.window_days}-day usage`;
    setVisible(elements.usersLoading, false);
    setVisible(elements.usersError, false);
    setVisible(elements.usersEmpty, state.users.length === 0);
    setVisible(elements.usersTable, state.users.length > 0);

    state.users.forEach((user) => {
      const row = document.createElement("tr");
      const identity = document.createElement("td");
      const button = appendText(identity, "button", "table-link", user.external_id);
      button.type = "button";
      button.addEventListener("click", () => openUser(user.id, button));
      appendText(identity, "span", "cell-subtle", shortId(user.id));
      row.appendChild(identity);

      const team = document.createElement("td");
      appendText(team, "span", "request-name", user.team_name || "Unassigned");
      if (user.team_id) appendText(team, "span", "cell-subtle", shortId(user.team_id));
      row.appendChild(team);

      const status = document.createElement("td");
      status.appendChild(statusBadge(user.is_active ? "active" : "disabled"));
      row.appendChild(status);

      const limits = document.createElement("td");
      appendText(limits, "span", "request-name", `${number(user.limits.rpm)} RPM`);
      appendText(limits, "span", "cell-subtle", `${number(user.limits.tpm)} TPM`);
      row.appendChild(limits);

      const usage = document.createElement("td");
      appendText(usage, "span", "request-name", `${number(user.usage.total_tokens)} tokens`);
      appendText(usage, "span", "cell-subtle", `${integer(user.usage.requests)} requests · ${relativeTime(user.usage.last_activity_at)}`);
      row.appendChild(usage);
      appendText(row, "td", "money-cell", money(user.usage.cost_usd));

      const keys = document.createElement("td");
      appendText(keys, "span", "request-name", `${integer(user.keys.active)} active`);
      appendText(keys, "span", "cell-subtle", `${integer(user.keys.total)} total`);
      row.appendChild(keys);

      const action = document.createElement("td");
      const open = appendText(action, "button", "row-open", "›");
      open.type = "button";
      open.setAttribute("aria-label", `Inspect ${user.external_id}`);
      open.addEventListener("click", () => openUser(user.id, open));
      row.appendChild(action);
      elements.userRows.appendChild(row);
    });
  }

  async function loadUsers() {
    setVisible(elements.usersLoading, true);
    setVisible(elements.usersError, false);
    setVisible(elements.usersEmpty, false);
    setVisible(elements.usersTable, false);
    elements.usersRefresh.disabled = true;
    try {
      const params = new URLSearchParams({
        days: elements.usersRange.value,
        limit: "100",
      });
      const query = elements.userSearch.value.trim();
      if (query) params.set("q", query);
      renderUsers(await api(`/admin/api/users?${params}`));
      state.usersLoaded = true;
    } catch (error) {
      setVisible(elements.usersLoading, false);
      elements.usersError.textContent = `Could not load users. ${error.message}`;
      setVisible(elements.usersError, true);
    } finally {
      elements.usersRefresh.disabled = false;
    }
  }

  function parseJsonObject(element, label) {
    let value;
    try {
      value = JSON.parse(element.value || "{}");
    } catch (error) {
      throw new Error(`${label} is not valid JSON. ${error.message}`);
    }
    if (!value || Array.isArray(value) || typeof value !== "object") {
      throw new Error(`${label} must be a JSON object.`);
    }
    return value;
  }

  function policyRuleSummary(document) {
    const rules = Array.isArray(document?.rules) ? document.rules : [];
    const counts = { allow: 0, require_approval: 0, deny: 0 };
    rules.forEach((rule) => {
      if (Object.hasOwn(counts, rule?.action)) counts[rule.action] += 1;
    });
    return `${integer(rules.length)} rules · ${counts.allow} allow · ${counts.require_approval} approval · ${counts.deny} deny · default ${document?.default_action || "deny"}`;
  }

  function fillPolicySelectors() {
    const options = [
      { value: "", label: "Active policy" },
      ...state.policies.map((policy) => ({ value: policy.version, label: policy.version })),
    ];
    const currentSimulation = elements.simVersion.value;
    elements.simVersion.replaceChildren();
    options.forEach((option) => {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      elements.simVersion.appendChild(element);
    });
    if (options.some((option) => option.value === currentSimulation)) elements.simVersion.value = currentSimulation;

    const currentBase = elements.draftBase.value;
    elements.draftBase.replaceChildren();
    state.policies.forEach((policy) => {
      const option = document.createElement("option");
      option.value = policy.version;
      option.textContent = policy.version === state.activePolicy?.version ? `${policy.version} (active)` : policy.version;
      elements.draftBase.appendChild(option);
    });
    if (state.activePolicy && !state.policies.some((policy) => policy.version === state.activePolicy.version)) {
      const option = document.createElement("option");
      option.value = state.activePolicy.version;
      option.textContent = `${state.activePolicy.version} (active)`;
      elements.draftBase.prepend(option);
    }
    elements.draftBase.value = state.policies.some((policy) => policy.version === currentBase)
      ? currentBase
      : state.activePolicy?.version || "";
  }

  function renderServers(items) {
    elements.serverInventory.replaceChildren();
    if (!items.length) {
      appendText(elements.serverInventory, "p", "mini-loading", "No MCP servers are configured.");
      return;
    }
    items.forEach((server) => {
      const card = document.createElement("article");
      card.className = "server-item";
      const heading = document.createElement("div");
      const title = document.createElement("div");
      appendText(title, "strong", "", server.name);
      appendText(title, "span", "cell-subtle", server.description || server.transport);
      heading.appendChild(title);
      heading.appendChild(statusBadge(server.status));
      card.appendChild(heading);
      appendText(
        card,
        "p",
        "server-meta",
        server.status === "healthy"
          ? `${integer(server.tool_count)} tools · ${integer(server.latency_ms)} ms`
          : server.error || "Server is disabled",
      );
      if (server.tools?.length) {
        const tools = document.createElement("div");
        tools.className = "server-tools";
        server.tools.slice(0, 8).forEach((tool) => {
          const button = appendText(tools, "button", "tool-chip", tool.name);
          button.type = "button";
          button.title = tool.description || tool.title || tool.name;
          button.addEventListener("click", () => {
            elements.simServer.value = server.name;
            elements.simTool.value = tool.name;
            elements.simArguments.focus();
          });
        });
        if (server.tools.length > 8) appendText(tools, "span", "tool-chip muted-chip", `+${server.tools.length - 8}`);
        card.appendChild(tools);
      }
      elements.serverInventory.appendChild(card);
    });
  }

  function renderPolicyHistory(activations) {
    elements.policyHistoryRows.replaceChildren();
    elements.policyHistorySummary.textContent = `${integer(state.policies.length)} immutable versions`;
    setVisible(elements.policyHistoryLoading, false);
    setVisible(elements.policyHistoryTable, state.policies.length > 0);
    state.policies.forEach((policy) => {
      const row = document.createElement("tr");
      const version = document.createElement("td");
      appendText(version, "span", "request-name", policy.version);
      appendText(version, "span", "cell-subtle", policy.base_version ? `Based on ${policy.base_version}` : "Root version");
      row.appendChild(version);
      const status = document.createElement("td");
      status.appendChild(statusBadge(policy.status));
      row.appendChild(status);
      appendText(row, "td", "", String(policy.document?.rules?.length || 0));
      appendText(row, "td", "", policy.source);
      const created = appendText(row, "td", "", relativeTime(policy.created_at));
      created.title = exactTime(policy.created_at);
      const change = document.createElement("td");
      appendText(change, "span", "policy-reason", policy.reason || "No change reason");
      appendText(change, "span", "cell-subtle", `${policy.diff_from_active?.length || 0} diff lines`);
      row.appendChild(change);
      const actions = document.createElement("td");
      const inspect = appendText(actions, "button", "btn btn-sm btn-outline-secondary", "Inspect");
      inspect.type = "button";
      inspect.addEventListener("click", () => openPolicy(policy, inspect));
      if (adminRole === "admin" && policy.version !== state.activePolicy?.version) {
        const activate = appendText(actions, "button", "btn btn-sm btn-primary policy-activate", "Activate");
        activate.type = "button";
        activate.addEventListener("click", () => activatePolicy(policy, activate));
      }
      row.appendChild(actions);
      elements.policyHistoryRows.appendChild(row);
    });

    elements.activationHistory.replaceChildren();
    if (activations?.length) {
      appendText(elements.activationHistory, "h3", "", "Recent activations");
      activations.slice(0, 6).forEach((activation) => {
        const item = document.createElement("div");
        appendText(item, "strong", "", activation.version);
        appendText(item, "span", "", `${relativeTime(activation.created_at)} · ${activation.reason || "No reason"}`);
        elements.activationHistory.appendChild(item);
      });
    }
  }

  function renderPolicies(payload) {
    state.policies = Array.isArray(payload.items) ? payload.items : [];
    state.activePolicy = payload.active;
    elements.policyActiveVersion.textContent = payload.active.version;
    elements.policyActiveSource.textContent = payload.active.source;
    elements.policyActiveSource.className = "status-badge status-active";
    elements.policyActiveSummary.textContent = policyRuleSummary(payload.active.document);
    renderPolicyHistory(payload.activations || []);
    fillPolicySelectors();
    if (!elements.draftDocument.value.trim()) {
      elements.draftDocument.value = JSON.stringify(payload.active.document || { default_action: "deny", rules: [] }, null, 2);
    }
  }

  async function loadPolicies() {
    elements.policiesRefresh.disabled = true;
    elements.policiesError.hidden = true;
    setVisible(elements.policyHistoryLoading, true);
    const [policiesResult, serversResult] = await Promise.allSettled([
      api("/admin/api/mcp/policies"),
      api("/admin/api/mcp/servers"),
    ]);
    if (policiesResult.status === "fulfilled") {
      renderPolicies(policiesResult.value);
      state.policiesLoaded = true;
    } else {
      elements.policiesError.textContent = `Could not load MCP policies. ${policiesResult.reason.message}`;
      elements.policiesError.hidden = false;
      setVisible(elements.policyHistoryLoading, false);
    }
    if (serversResult.status === "fulfilled") {
      renderServers(serversResult.value.items || []);
    } else {
      elements.serverInventory.replaceChildren();
      appendText(elements.serverInventory, "p", "queue-error mini-loading", `Server checks failed. ${serversResult.reason.message}`);
    }
    elements.policiesUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    })}`;
    elements.policiesRefresh.disabled = false;
  }

  function openPolicy(policy, focusTarget) {
    openDrawer(focusTarget);
    elements.drawerEyebrow.textContent = "MCP policy version";
    elements.drawerTitle.textContent = policy.version;
    elements.drawerActions.hidden = true;
    elements.drawerContent.replaceChildren();
    const hero = document.createElement("section");
    hero.className = "detail-hero";
    hero.appendChild(statusBadge(policy.status));
    appendText(hero, "h3", "", policy.version);
    appendText(hero, "span", "cell-subtle", policy.reason || "No change reason supplied");
    elements.drawerContent.appendChild(hero);
    const grid = document.createElement("div");
    grid.className = "detail-grid";
    detailField(grid, "Source", policy.source);
    detailField(grid, "Base version", policy.base_version || "None");
    detailField(grid, "Created by", policy.created_by || "Configuration");
    detailField(grid, "Created", exactTime(policy.created_at));
    elements.drawerContent.appendChild(grid);
    const documentSection = document.createElement("section");
    documentSection.className = "detail-section";
    appendText(documentSection, "h3", "", "Policy document");
    appendText(documentSection, "pre", "argument-block", JSON.stringify(policy.document || {}, null, 2));
    elements.drawerContent.appendChild(documentSection);
    const diff = document.createElement("section");
    diff.className = "detail-section";
    appendText(diff, "h3", "", "Diff from active");
    appendText(diff, "pre", "argument-block policy-diff", (policy.diff_from_active || []).join("\n") || "No differences");
    elements.drawerContent.appendChild(diff);
  }

  async function simulatePolicy(event) {
    event.preventDefault();
    elements.simError.hidden = true;
    elements.simSubmit.disabled = true;
    try {
      const argumentsValue = parseJsonObject(elements.simArguments, "Arguments");
      const payload = await api("/admin/api/mcp/policies/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: elements.simUser.value.trim(),
          team_id: elements.simTeam.value.trim() || null,
          scopes: elements.simScopes.value.split(",").map((scope) => scope.trim()).filter(Boolean),
          server: elements.simServer.value.trim(),
          tool: elements.simTool.value.trim(),
          arguments: argumentsValue,
          version: elements.simVersion.value || null,
        }),
      });
      elements.simulationResult.replaceChildren();
      const header = document.createElement("div");
      header.appendChild(statusBadge(payload.effective_action));
      appendText(header, "strong", "", payload.rule_name || "Default policy action");
      elements.simulationResult.appendChild(header);
      appendText(elements.simulationResult, "p", "", payload.reason);
      appendText(
        elements.simulationResult,
        "span",
        "cell-subtle",
        `${payload.policy_version} · ${payload.policy_source}${payload.standing_grant ? ` · grant ${shortId(payload.standing_grant.id)}` : ""}`,
      );
      if (Object.keys(payload.constraints || {}).length) {
        appendText(elements.simulationResult, "pre", "argument-block", JSON.stringify(payload.constraints, null, 2));
      }
      elements.simulationResult.hidden = false;
    } catch (error) {
      elements.simError.textContent = error.message;
      elements.simError.hidden = false;
    } finally {
      elements.simSubmit.disabled = false;
    }
  }

  function renderValidation(payload) {
    elements.draftValidation.replaceChildren();
    const heading = document.createElement("div");
    heading.appendChild(statusBadge(payload.valid ? "valid" : "invalid"));
    appendText(heading, "strong", "", payload.valid ? "Policy is valid" : "Policy needs changes");
    elements.draftValidation.appendChild(heading);
    [...(payload.errors || []), ...(payload.warnings || [])].forEach((message) => {
      appendText(elements.draftValidation, "p", "", message);
    });
    if (payload.diff_from_active?.length) {
      appendText(elements.draftValidation, "pre", "argument-block policy-diff", payload.diff_from_active.join("\n"));
    }
    elements.draftValidation.hidden = false;
  }

  async function validateDraft() {
    elements.draftError.hidden = true;
    elements.draftValidate.disabled = true;
    try {
      const documentValue = parseJsonObject(elements.draftDocument, "Policy document");
      renderValidation(await api("/admin/api/mcp/policies/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document: documentValue }),
      }));
    } catch (error) {
      elements.draftError.textContent = error.message;
      elements.draftError.hidden = false;
    } finally {
      elements.draftValidate.disabled = false;
    }
  }

  async function saveDraft(event) {
    event.preventDefault();
    elements.draftError.hidden = true;
    elements.draftSave.disabled = true;
    try {
      const documentValue = parseJsonObject(elements.draftDocument, "Policy document");
      await api("/admin/api/mcp/policies/drafts", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({
          version: elements.draftVersion.value.trim(),
          base_version: elements.draftBase.value || null,
          reason: elements.draftReason.value.trim(),
          document: documentValue,
        }),
      });
      showToast("Policy draft saved.");
      elements.draftVersion.value = "";
      elements.draftReason.value = "";
      await loadPolicies();
    } catch (error) {
      elements.draftError.textContent = error.message;
      elements.draftError.hidden = false;
    } finally {
      elements.draftSave.disabled = false;
    }
  }

  async function activatePolicy(policy, button) {
    const reason = window.prompt(`Why should policy ${policy.version} become active?`);
    if (reason === null) return;
    if (reason.trim().length < 3) {
      showToast("Activation requires a short audit reason.");
      return;
    }
    button.disabled = true;
    try {
      await api(`/admin/api/mcp/policies/${encodeURIComponent(policy.version)}/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      showToast(`Policy ${policy.version} is now active.`);
      state.policiesLoaded = false;
      await loadPolicies();
      if (state.grantsLoaded) await loadGrants({ quiet: true });
    } catch (error) {
      showToast(`Could not activate policy: ${error.message}`);
      button.disabled = false;
    }
  }

  function renderGrants() {
    elements.grantRows.replaceChildren();
    elements.grantsSummary.textContent = `${integer(state.grants.length)} ${state.grants.length === 1 ? "grant" : "grants"}`;
    setVisible(elements.grantsLoading, false);
    setVisible(elements.grantsError, false);
    setVisible(elements.grantsEmpty, state.grants.length === 0);
    setVisible(elements.grantsTable, state.grants.length > 0);

    state.grants.forEach((grant) => {
      const row = document.createElement("tr");
      const scope = document.createElement("td");
      appendText(scope, "span", "request-name", grant.subject_type === "team" ? "Team" : "User");
      const subject = appendText(scope, "span", "cell-subtle", shortId(grant.subject_id));
      subject.title = grant.subject_id;
      row.appendChild(scope);

      const tool = document.createElement("td");
      appendText(tool, "span", "request-name", grant.tool);
      appendText(tool, "span", "cell-subtle", `${grant.server} · policy ${grant.policy_version}`);
      row.appendChild(tool);

      const usage = document.createElement("td");
      appendText(usage, "span", "request-name", `${integer(grant.calls_used)} / ${integer(grant.max_calls)}`);
      appendText(usage, "span", "cell-subtle", `${integer(grant.calls_remaining)} remaining`);
      const progress = document.createElement("span");
      progress.className = "grant-progress";
      const fill = document.createElement("i");
      fill.style.width = `${Math.min(100, (grant.calls_used / Math.max(grant.max_calls, 1)) * 100)}%`;
      progress.appendChild(fill);
      usage.appendChild(progress);
      row.appendChild(usage);

      const expires = appendText(row, "td", "", relativeTime(grant.expires_at));
      expires.title = exactTime(grant.expires_at);
      const status = document.createElement("td");
      status.appendChild(statusBadge(grant.status));
      row.appendChild(status);

      const reason = document.createElement("td");
      appendText(reason, "span", "grant-reason", grant.reason || "No reason supplied");
      const provenance = grant.workflow_id
        ? `Workflow ${grant.workflow_id}`
        : grant.source_approval_id
          ? `Approval ${shortId(grant.source_approval_id)}`
          : `Created by ${grant.created_by || "unknown"}`;
      appendText(reason, "span", "cell-subtle", provenance);
      row.appendChild(reason);

      const action = document.createElement("td");
      if (adminRole === "admin" && grant.status === "active") {
        const revoke = appendText(action, "button", "btn btn-sm btn-outline-danger grant-revoke", "Revoke");
        revoke.type = "button";
        revoke.addEventListener("click", () => revokeGrant(grant, revoke));
      }
      row.appendChild(action);
      elements.grantRows.appendChild(row);
    });
  }

  async function loadGrants({ quiet = false } = {}) {
    if (!quiet) {
      setVisible(elements.grantsLoading, true);
      setVisible(elements.grantsError, false);
      setVisible(elements.grantsEmpty, false);
      setVisible(elements.grantsTable, false);
    }
    elements.grantsRefresh.disabled = true;
    try {
      const includeInactive = elements.grantsShowInactive.checked ? "true" : "false";
      const payload = await api(`/admin/api/mcp/grants?include_inactive=${includeInactive}&limit=200`);
      state.grants = Array.isArray(payload.items) ? payload.items : [];
      renderGrants();
      elements.grantsUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })}`;
      state.grantsLoaded = true;
    } catch (error) {
      if (!quiet) {
        setVisible(elements.grantsLoading, false);
        elements.grantsError.textContent = `Could not load approval grants. ${error.message}`;
        setVisible(elements.grantsError, true);
      }
      elements.grantsUpdated.textContent = "Refresh failed";
    } finally {
      elements.grantsRefresh.disabled = false;
    }
  }

  async function createGrant(event) {
    event.preventDefault();
    let constraints = {};
    const rawConstraints = elements.grantConstraints.value.trim();
    try {
      constraints = rawConstraints ? JSON.parse(rawConstraints) : {};
      if (!constraints || Array.isArray(constraints) || typeof constraints !== "object") {
        throw new Error("Constraints must be a JSON object.");
      }
    } catch (error) {
      elements.grantFormError.textContent = error.message.startsWith("Constraints")
        ? error.message
        : `Constraints are not valid JSON. ${error.message}`;
      elements.grantFormError.hidden = false;
      elements.grantConstraints.focus();
      return;
    }
    elements.grantCreateButton.disabled = true;
    elements.grantFormError.hidden = true;
    try {
      await api("/admin/api/mcp/grants", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({
          subject_type: elements.grantSubjectType.value,
          subject_id: elements.grantSubjectId.value.trim(),
          server: elements.grantServer.value.trim(),
          tool: elements.grantTool.value.trim(),
          constraints,
          ttl_seconds: Number(elements.grantTtl.value),
          max_calls: Number(elements.grantMaxCalls.value),
          workflow_id: elements.grantWorkflow.value.trim() || null,
          reason: elements.grantReason.value.trim(),
        }),
      });
      elements.grantCreateForm.reset();
      elements.grantMaxCalls.value = "20";
      showToast("Standing approval grant created.");
      await loadGrants({ quiet: true });
    } catch (error) {
      elements.grantFormError.textContent = error.message;
      elements.grantFormError.hidden = false;
    } finally {
      elements.grantCreateButton.disabled = false;
    }
  }

  async function revokeGrant(grant, button) {
    if (!window.confirm(`Revoke access to ${grant.server}/${grant.tool}?`)) return;
    button.disabled = true;
    try {
      await api(`/admin/api/mcp/grants/${encodeURIComponent(grant.id)}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      });
      showToast("Approval grant revoked immediately.");
      await loadGrants({ quiet: true });
    } catch (error) {
      showToast(`Could not revoke grant: ${error.message}`);
      button.disabled = false;
    }
  }

  function detailField(grid, label, value) {
    const wrapper = document.createElement("div");
    wrapper.className = "detail-field";
    const list = document.createElement("dl");
    appendText(list, "dt", "", label);
    appendText(list, "dd", "", value ?? "—");
    wrapper.appendChild(list);
    grid.appendChild(wrapper);
  }

  function detailTable(parent, heading, columns, rows) {
    const section = document.createElement("section");
    section.className = "detail-section";
    appendText(section, "h3", "", heading);
    const wrapper = document.createElement("div");
    wrapper.className = "table-responsive";
    const table = document.createElement("table");
    table.className = "data-table compact-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((column) => appendText(headRow, "th", "", column));
    head.appendChild(headRow);
    table.appendChild(head);
    const body = document.createElement("tbody");
    rows.forEach((values) => {
      const row = document.createElement("tr");
      values.forEach((value) => appendText(row, "td", "", value));
      body.appendChild(row);
    });
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = appendText(row, "td", "empty-cell", "No records in this period");
      cell.colSpan = columns.length;
      body.appendChild(row);
    }
    table.appendChild(body);
    wrapper.appendChild(table);
    section.appendChild(wrapper);
    parent.appendChild(section);
  }

  function renderUserDetail(user) {
    elements.drawerEyebrow.textContent = "User profile";
    elements.drawerTitle.textContent = user.external_id;
    elements.drawerContent.replaceChildren();
    elements.drawerActions.hidden = true;

    const hero = document.createElement("section");
    hero.className = "detail-hero";
    hero.appendChild(statusBadge(user.is_active ? "active" : "disabled"));
    appendText(hero, "h3", "", user.external_id);
    appendText(hero, "span", "cell-subtle", user.team_name || "No team assigned");
    elements.drawerContent.appendChild(hero);

    const metrics = document.createElement("div");
    metrics.className = "detail-metrics";
    [
      ["Requests", integer(user.usage.requests)],
      ["Tokens", number(user.usage.total_tokens)],
      ["Cost", money(user.usage.cost_usd)],
      ["Active keys", integer(user.keys.active)],
    ].forEach(([label, value]) => {
      const card = document.createElement("div");
      appendText(card, "span", "", label);
      appendText(card, "strong", "", value);
      metrics.appendChild(card);
    });
    elements.drawerContent.appendChild(metrics);

    const grid = document.createElement("div");
    grid.className = "detail-grid";
    detailField(grid, "Relay user ID", user.id);
    detailField(grid, "Created", exactTime(user.created_at));
    detailField(grid, "Last activity", exactTime(user.usage.last_activity_at));
    detailField(grid, "Average latency", `${integer(Math.round(user.usage.avg_latency_ms))} ms`);
    detailField(grid, "Request limit", `${integer(user.limits.rpm)} RPM${user.limits.rpm_custom ? " · custom" : " · default"}`);
    detailField(grid, "Token limit", `${integer(user.limits.tpm)} TPM${user.limits.tpm_custom ? " · custom" : " · default"}`);
    detailField(grid, "Daily token limit", integer(user.limits.tokens_per_day));
    detailField(grid, "Team limit", user.limits.team_tpm ? `${integer(user.limits.team_tpm)} TPM` : "No team");
    elements.drawerContent.appendChild(grid);

    detailTable(
      elements.drawerContent,
      "Model usage",
      ["Model", "Requests", "Tokens", "Cost"],
      (user.models || []).map((model) => [
        model.model,
        integer(model.requests),
        number(model.total_tokens),
        money(model.cost_usd),
      ]),
    );
    detailTable(
      elements.drawerContent,
      "API keys",
      ["Key", "Status", "Scopes", "Last used"],
      (user.api_keys || []).map((key) => [
        `${key.name} · ${key.key_prefix}`,
        key.status,
        (key.scopes || []).join(", ") || "None",
        relativeTime(key.last_used_at),
      ]),
    );
  }

  async function openUser(userId, focusTarget) {
    openDrawer(focusTarget);
    elements.drawerEyebrow.textContent = "User profile";
    elements.drawerTitle.textContent = "Loading user…";
    elements.drawerActions.hidden = true;
    elements.drawerContent.replaceChildren();
    const loading = appendText(elements.drawerContent, "div", "queue-state drawer-loading", "Loading user details");
    loading.setAttribute("aria-live", "polite");
    try {
      const user = await api(`/admin/api/users/${encodeURIComponent(userId)}?days=${elements.usersRange.value}`);
      renderUserDetail(user);
    } catch (error) {
      elements.drawerTitle.textContent = "User unavailable";
      elements.drawerContent.replaceChildren();
      appendText(elements.drawerContent, "div", "alert alert-danger", error.message);
    }
  }

  const displayApprovalStatus = (item) => item.status || "pending";

  function filteredApprovals() {
    if (!state.approvalStatus) return state.approvals;
    if (state.approvalStatus === "approved") {
      return state.approvals.filter((item) => ["approved", "consumed"].includes(displayApprovalStatus(item)));
    }
    return state.approvals.filter((item) => displayApprovalStatus(item) === state.approvalStatus);
  }

  function renderApprovalStats() {
    const count = (statuses) => state.approvals.filter((item) => statuses.includes(displayApprovalStatus(item))).length;
    byId("stat-pending").textContent = String(count(["pending"]));
    byId("stat-approved").textContent = String(count(["approved", "consumed"]));
    byId("stat-denied").textContent = String(count(["denied"]));
    const soon = state.approvals.filter((item) => {
      if (displayApprovalStatus(item) !== "pending") return false;
      const expiry = dateValue(item.expires_at)?.valueOf() || 0;
      return expiry > Date.now() && expiry - Date.now() <= 5 * 60 * 1000;
    }).length;
    byId("stat-expiring").textContent = String(soon);
  }

  function renderApprovalTable() {
    const items = filteredApprovals();
    elements.approvalRows.replaceChildren();
    elements.approvalSummary.textContent = `${items.length} ${items.length === 1 ? "request" : "requests"} in this view`;
    setVisible(elements.approvalLoading, false);
    setVisible(elements.approvalError, false);
    setVisible(elements.approvalEmpty, items.length === 0);
    setVisible(elements.approvalTable, items.length > 0);

    items.forEach((item) => {
      const row = document.createElement("tr");
      const request = document.createElement("td");
      appendText(request, "span", "request-name", item.tool || "Unknown tool");
      appendText(request, "span", "cell-subtle", `via ${item.server || "unknown"}`);
      row.appendChild(request);
      const requester = document.createElement("td");
      appendText(requester, "span", "request-name", shortId(item.user_id));
      appendText(requester, "span", "cell-subtle", item.team_id ? `Team ${shortId(item.team_id)}` : "No team");
      row.appendChild(requester);
      const policy = document.createElement("td");
      appendText(policy, "span", "request-name", item.policy_version || "—");
      appendText(policy, "span", "cell-subtle", shortId(item.id));
      row.appendChild(policy);
      const requested = appendText(row, "td", "", relativeTime(item.requested_at));
      requested.title = exactTime(item.requested_at);
      const status = document.createElement("td");
      status.appendChild(statusBadge(displayApprovalStatus(item)));
      row.appendChild(status);
      const action = document.createElement("td");
      const button = appendText(action, "button", "row-open", "›");
      button.type = "button";
      button.setAttribute("aria-label", `Review ${item.tool || "tool"} request`);
      button.addEventListener("click", () => openApproval(item.id, button));
      row.appendChild(action);
      elements.approvalRows.appendChild(row);
    });
  }

  function renderApprovalDetail(item) {
    elements.drawerEyebrow.textContent = "Approval request";
    elements.drawerTitle.textContent = "Review tool call";
    elements.drawerContent.replaceChildren();
    const hero = document.createElement("section");
    hero.className = "detail-hero";
    hero.appendChild(statusBadge(displayApprovalStatus(item)));
    appendText(hero, "h3", "", item.tool || "Unknown tool");
    appendText(hero, "span", "cell-subtle", `via ${item.server || "unknown"}`);
    elements.drawerContent.appendChild(hero);
    const grid = document.createElement("div");
    grid.className = "detail-grid";
    detailField(grid, "Requester", shortId(item.user_id));
    detailField(grid, "Team", item.team_id ? shortId(item.team_id) : "No team");
    detailField(grid, "Policy version", item.policy_version);
    detailField(grid, "Approval ID", item.id);
    detailField(grid, "Requested", exactTime(item.requested_at));
    detailField(grid, "Expires", exactTime(item.expires_at));
    elements.drawerContent.appendChild(grid);
    const purpose = document.createElement("section");
    purpose.className = "detail-section";
    appendText(purpose, "h3", "", "Purpose");
    appendText(purpose, "p", "purpose-block", item.purpose || "No purpose was supplied by the requester.");
    elements.drawerContent.appendChild(purpose);
    const args = document.createElement("section");
    args.className = "detail-section";
    appendText(args, "h3", "", "Exact arguments");
    appendText(args, "pre", "argument-block", JSON.stringify(item.arguments || {}, null, 2));
    elements.drawerContent.appendChild(args);
    if (item.grant_offer) {
      const offer = item.grant_offer;
      const standing = document.createElement("section");
      standing.className = "detail-section grant-offer";
      appendText(standing, "h3", "", "Standing access offered");
      const ttlHours = Number(offer.ttl_seconds || 3600) / 3600;
      appendText(
        standing,
        "p",
        "purpose-block",
        `Approving also creates a ${offer.subject || "user"} grant for ${integer(offer.max_calls || 1)} calls over ${ttlHours} hours.`,
      );
      if (offer.tool_pattern) detailField(standing, "Tool pattern", offer.tool_pattern);
      if (offer.workflow_id) detailField(standing, "Workflow", offer.workflow_id);
      if (Object.keys(offer.constraints || {}).length) {
        appendText(standing, "pre", "argument-block", JSON.stringify(offer.constraints, null, 2));
      }
      elements.drawerContent.appendChild(standing);
    }
    if (item.decision_reason || item.decided_by) {
      const decision = document.createElement("section");
      decision.className = "detail-section";
      appendText(decision, "h3", "", "Decision");
      appendText(decision, "p", "decision-summary", `${item.decided_by || "Unknown actor"}: ${item.decision_reason || "No reason supplied"}`);
      elements.drawerContent.appendChild(decision);
    }
    const canDecide = displayApprovalStatus(item) === "pending" && ["approver", "admin"].includes(adminRole);
    elements.drawerActions.hidden = !canDecide;
    elements.reason.value = "";
    elements.decisionError.hidden = true;
  }

  function openDrawer(focusTarget) {
    state.lastFocus = focusTarget;
    elements.backdrop.hidden = false;
    elements.drawer.classList.add("open");
    elements.drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    window.setTimeout(() => elements.drawerClose.focus(), 30);
  }

  function openApproval(id, focusTarget) {
    const item = state.approvals.find((entry) => entry.id === id);
    if (!item) return;
    state.selectedApprovalId = id;
    openDrawer(focusTarget);
    renderApprovalDetail(item);
  }

  function closeDrawer() {
    elements.drawer.classList.remove("open");
    elements.drawer.setAttribute("aria-hidden", "true");
    elements.backdrop.hidden = true;
    document.body.style.overflow = "";
    state.selectedApprovalId = null;
    state.lastFocus?.focus?.();
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => { elements.toast.hidden = true; }, 3500);
  }

  async function loadApprovals({ quiet = false } = {}) {
    if (!quiet) {
      setVisible(elements.approvalLoading, true);
      setVisible(elements.approvalError, false);
      setVisible(elements.approvalEmpty, false);
      setVisible(elements.approvalTable, false);
    }
    elements.approvalRefresh.disabled = true;
    try {
      const payload = await api("/admin/api/mcp/approvals?status=&limit=200");
      state.approvals = Array.isArray(payload.items) ? payload.items : [];
      renderApprovalStats();
      renderApprovalTable();
      elements.approvalUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })}`;
      state.approvalsLoaded = true;
      if (state.selectedApprovalId) {
        const selected = state.approvals.find((item) => item.id === state.selectedApprovalId);
        if (selected) renderApprovalDetail(selected);
      }
    } catch (error) {
      if (!quiet) {
        setVisible(elements.approvalLoading, false);
        setVisible(elements.approvalTable, false);
        elements.approvalError.textContent = `Could not load approvals. ${error.message}`;
        setVisible(elements.approvalError, true);
      }
      elements.approvalUpdated.textContent = "Refresh failed";
    } finally {
      elements.approvalRefresh.disabled = false;
    }
  }

  async function decide(decision) {
    const item = state.approvals.find((entry) => entry.id === state.selectedApprovalId);
    if (!item) return;
    const reason = elements.reason.value.trim();
    if (reason.length < 3) {
      elements.decisionError.textContent = "Add a short reason for the audit log.";
      elements.decisionError.hidden = false;
      elements.reason.focus();
      return;
    }
    elements.approve.disabled = true;
    elements.deny.disabled = true;
    try {
      await api(`/admin/api/mcp/approvals/${encodeURIComponent(item.id)}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ decision, reason }),
      });
      closeDrawer();
      const approvedMessage = item.grant_offer
        ? "Approved and standing grant created."
        : "Tool call approved once.";
      showToast(decision === "approved" ? approvedMessage : "Tool call denied.");
      await loadApprovals({ quiet: true });
    } catch (error) {
      elements.decisionError.textContent = error.message;
      elements.decisionError.hidden = false;
    } finally {
      elements.approve.disabled = false;
      elements.deny.disabled = false;
    }
  }

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  document.querySelectorAll(".filter-tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab").forEach((tab) => {
        const selected = tab === button;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", selected ? "true" : "false");
      });
      state.approvalStatus = button.dataset.status || "";
      renderApprovalTable();
    });
  });

  elements.overviewRefresh.addEventListener("click", loadOverview);
  elements.overviewRange.addEventListener("change", loadOverview);
  elements.usersRefresh.addEventListener("click", loadUsers);
  elements.usersRange.addEventListener("change", loadUsers);
  elements.userSearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadUsers();
  });
  elements.approvalRefresh.addEventListener("click", () => loadApprovals());
  elements.policiesRefresh.addEventListener("click", loadPolicies);
  elements.simulatorForm.addEventListener("submit", simulatePolicy);
  elements.draftValidate.addEventListener("click", validateDraft);
  elements.policyDraftForm.addEventListener("submit", saveDraft);
  elements.grantsRefresh.addEventListener("click", () => loadGrants());
  elements.grantsShowInactive.addEventListener("change", () => loadGrants());
  elements.grantCreateForm.addEventListener("submit", createGrant);
  elements.drawerClose.addEventListener("click", closeDrawer);
  elements.backdrop.addEventListener("click", closeDrawer);
  elements.approve.addEventListener("click", () => decide("approved"));
  elements.deny.addEventListener("click", () => decide("denied"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.drawer.classList.contains("open")) closeDrawer();
  });

  elements.grantCreateCard.hidden = adminRole !== "admin";
  elements.policyDraftCard.hidden = adminRole !== "admin";
  loadOverview();
  window.setInterval(() => {
    if (state.view === "overview") loadOverview();
    if (state.view === "grants") loadGrants({ quiet: true });
    if (state.view === "approvals") loadApprovals({ quiet: true });
  }, 30000);
})();
