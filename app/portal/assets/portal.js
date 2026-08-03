(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="relay-csrf"]')?.content || "";
  const state = { overview: null, days: 30 };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const number = new Intl.NumberFormat();
  const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 4 });

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body) headers["Content-Type"] = "application/json";
    if (options.method && options.method !== "GET") headers["X-Relay-CSRF"] = csrf;
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    if (response.status === 401) {
      window.location.assign("/portal/login");
      throw new Error("Your session expired");
    }
    const body = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.detail || "Relay could not complete that request");
    return body;
  }

  function showError(error) {
    const box = $("#global-error");
    box.textContent = error.message || String(error);
    box.hidden = false;
  }

  function formatDate(value, fallback = "Never") {
    if (!value) return fallback;
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
  }

  function formatCompact(value) {
    return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
  }

  function percent(used, limit) {
    return limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  }

  function renderUsage(data) {
    const usage = data.usage;
    const limits = data.limits;
    $("#stat-requests").textContent = number.format(usage.requests);
    $("#stat-errors").textContent = `${number.format(usage.errors)} errors · ${state.days} days`;
    $("#stat-tokens").textContent = formatCompact(usage.total_tokens);
    $("#stat-cost").textContent = `${money.format(usage.cost_usd)} estimated cost`;
    $("#stat-daily").textContent = `${percent(limits.tokens_today, limits.tokens_per_day)}%`;
    $("#stat-daily-detail").textContent = `${formatCompact(limits.tokens_today)} of ${formatCompact(limits.tokens_per_day)} tokens today`;
    $("#daily-meter").style.width = `${percent(limits.tokens_today, limits.tokens_per_day)}%`;
    $("#stat-keys").textContent = String(data.keys.active);
    $("#stat-keys-detail").textContent = `${data.max_active_keys} active keys allowed`;

    const limitRows = [
      ["Requests / minute", `${number.format(limits.requests_last_minute)} / ${number.format(limits.rpm)}`],
      ["Tokens / minute", `${formatCompact(limits.tokens_last_minute)} / ${formatCompact(limits.tpm)}`],
      ["Tokens / day", `${formatCompact(limits.tokens_today)} / ${formatCompact(limits.tokens_per_day)}`],
    ];
    if (limits.team_tpm) limitRows.push(["Team TPM", formatCompact(limits.team_tpm)]);
    if (limits.team_tokens_per_day) limitRows.push(["Team daily", `${formatCompact(limits.team_tokens_today)} / ${formatCompact(limits.team_tokens_per_day)}`]);
    $("#limits-list").replaceChildren(...limitRows.map(([label, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = value; row.append(dt, dd); return row;
    }));

    const chart = $("#usage-chart");
    const max = Math.max(1, ...data.daily.map((item) => item.total_tokens));
    chart.replaceChildren(...data.daily.map((item) => {
      const col = document.createElement("div"); col.className = "bar-column";
      const bar = document.createElement("i"); bar.style.height = `${Math.max(1, (item.total_tokens / max) * 100)}%`;
      bar.title = `${item.period}: ${number.format(item.total_tokens)} tokens`;
      const label = document.createElement("span"); label.textContent = item.period.slice(5);
      col.append(bar, label); return col;
    }));
    if (!data.daily.length) chart.textContent = "No usage in this period.";

    const models = $("#models-list");
    models.replaceChildren(...data.models.slice(0, 7).map((item) => {
      const row = document.createElement("div"); row.className = "model-row";
      const name = document.createElement("strong"); name.textContent = item.model;
      const requests = document.createElement("span"); requests.textContent = `${number.format(item.requests)} requests`;
      const tokens = document.createElement("span"); tokens.textContent = `${formatCompact(item.total_tokens)} tokens`;
      row.append(name, requests, tokens); return row;
    }));
    if (!data.models.length) models.textContent = "No model usage yet.";
  }

  function scopePills(scopes) {
    const wrap = document.createElement("div"); wrap.className = "scope-list";
    scopes.forEach((scope) => { const pill = document.createElement("span"); pill.className = "scope-pill"; pill.textContent = scope; wrap.append(pill); });
    return wrap;
  }

  function renderKeys(data) {
    const body = $("#keys-body");
    const keys = data.api_keys || [];
    body.replaceChildren(...keys.map((key) => {
      const row = document.createElement("tr");
      const name = document.createElement("td"); name.textContent = key.name;
      const prefix = document.createElement("td"); const code = document.createElement("code"); code.textContent = `${key.key_prefix}…`; prefix.append(code);
      const scopes = document.createElement("td"); scopes.append(scopePills(key.scopes));
      const used = document.createElement("td"); used.textContent = formatDate(key.last_used_at);
      const expires = document.createElement("td"); expires.textContent = formatDate(key.expires_at, "No expiry");
      const status = document.createElement("td"); const badge = document.createElement("span"); badge.className = `status-pill ${key.status}`; badge.textContent = key.status; status.append(badge);
      const actions = document.createElement("td"); actions.className = "key-actions";
      if (key.status === "active") {
        const rotate = document.createElement("button"); rotate.type = "button"; rotate.textContent = "Rotate"; rotate.addEventListener("click", () => rotateKey(key));
        const revoke = document.createElement("button"); revoke.type = "button"; revoke.className = "danger"; revoke.textContent = "Revoke"; revoke.addEventListener("click", () => revokeKey(key));
        actions.append(rotate, revoke);
      }
      row.append(name, prefix, scopes, used, expires, status, actions); return row;
    }));
    $("#keys-empty").hidden = keys.length !== 0;
  }

  function renderScopeOptions(scopes) {
    $("#scope-options").replaceChildren(...scopes.map((scope) => {
      const label = document.createElement("label"); label.className = "scope-option";
      const input = document.createElement("input"); input.type = "checkbox"; input.value = scope; input.checked = ["chat", "responses"].includes(scope);
      const text = document.createElement("span"); text.textContent = scope; label.append(input, text); return label;
    }));
  }

  function renderGuides(data) {
    const base = data.base_url.replace(/\/$/, "");
    const key = "<YOUR_RELAY_KEY>";
    $("#snippet-openai").textContent = `from openai import OpenAI\n\nclient = OpenAI(\n    base_url="${base}/v1",\n    api_key="${key}",\n)\nresponse = client.chat.completions.create(\n    model="${data.default_model}",\n    messages=[{"role": "user", "content": "Hello"}],\n)`;
    $("#snippet-anthropic").textContent = `from anthropic import Anthropic\n\nclient = Anthropic(\n    base_url="${base}",\n    api_key="${key}",\n)\nmessage = client.messages.create(\n    model="${data.default_model}",\n    max_tokens=512,\n    messages=[{"role": "user", "content": "Hello"}],\n)`;
    $("#snippet-claude").textContent = `export ANTHROPIC_BASE_URL="${base}"\nexport ANTHROPIC_AUTH_TOKEN="${key}"\nclaude`;
    $("#snippet-mcp").textContent = JSON.stringify({ mcpServers: { relay: { type: "http", url: `${base}/mcp`, headers: { Authorization: `Bearer ${key}` } } } }, null, 2);
    $("#snippet-responses").textContent = `curl "${base}/v1/responses" \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${data.default_model}",\n    "input": "Run the project tests",\n    "relay_mcp_servers": ["code"],\n    "relay_mcp_purpose": "Validate my change"\n  }'`;
  }

  async function load() {
    try {
      const data = await api(`/portal/api/overview?days=${state.days}`);
      state.overview = data;
      renderUsage(data); renderKeys(data); renderScopeOptions(data.allowed_key_scopes); renderGuides(data);
    } catch (error) { showError(error); }
  }

  function revealSecret(secret) {
    $("#new-key-secret").textContent = secret;
    $("#secret-dialog").showModal();
  }

  async function createKey() {
    const errorBox = $("#key-form-error"); errorBox.hidden = true;
    if (!$("#key-form").reportValidity()) return;
    const scopes = $$("#scope-options input:checked").map((input) => input.value);
    try {
      const result = await api("/portal/api/keys", { method: "POST", body: JSON.stringify({ name: $("#key-name").value.trim(), scopes, expires_in_days: Number($("#key-ttl").value) }) });
      $("#key-dialog").close(); revealSecret(result.key); await load();
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  }

  async function rotateKey(key) {
    if (!window.confirm(`Rotate ${key.name}? The current key will stop working immediately.`)) return;
    try { const result = await api(`/portal/api/keys/${encodeURIComponent(key.id)}/rotate`, { method: "POST" }); revealSecret(result.key); await load(); } catch (error) { showError(error); }
  }

  async function revokeKey(key) {
    if (!window.confirm(`Revoke ${key.name}? This cannot be undone.`)) return;
    try { await api(`/portal/api/keys/${encodeURIComponent(key.id)}`, { method: "DELETE" }); await load(); } catch (error) { showError(error); }
  }

  $$(".nav-item[data-view]").forEach((button) => button.addEventListener("click", () => {
    $$(".nav-item[data-view]").forEach((item) => { item.classList.toggle("active", item === button); item.removeAttribute("aria-current"); });
    button.setAttribute("aria-current", "page");
    $$(".view-panel").forEach((panel) => { const active = panel.dataset.panel === button.dataset.view; panel.classList.toggle("active", active); panel.hidden = !active; });
  }));
  $$(".guide-tab").forEach((button) => button.addEventListener("click", () => {
    $$(".guide-tab").forEach((item) => item.classList.toggle("active", item === button));
    $$("[data-guide-panel]").forEach((panel) => { const active = panel.dataset.guidePanel === button.dataset.guide; panel.classList.toggle("active", active); panel.hidden = !active; });
  }));
  $$(".copy-code").forEach((button) => button.addEventListener("click", async () => { await navigator.clipboard.writeText($(`#${button.dataset.copy}`).textContent); button.textContent = "Copied"; setTimeout(() => { button.textContent = "Copy code"; }, 1500); }));
  $("#usage-range").addEventListener("change", (event) => { state.days = Number(event.target.value); load(); });
  $("#new-key-button").addEventListener("click", () => { $("#key-form").reset(); $("#key-dialog").showModal(); });
  $("#create-key-submit").addEventListener("click", createKey);
  $("#copy-secret").addEventListener("click", async () => { await navigator.clipboard.writeText($("#new-key-secret").textContent); $("#copy-secret").textContent = "Copied"; });
  $("#secret-done").addEventListener("click", () => { $("#new-key-secret").textContent = ""; $("#secret-dialog").close(); $("#copy-secret").textContent = "Copy"; });
  load();
})();
