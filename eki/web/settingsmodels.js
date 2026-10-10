// Settings: who eki's own jobs go to (routing.json "roles") and each provider's model tiers
// (providers.json "models"), over GET /api/models (eki/api_models.py). settings.js puts roles(d)
// at the top of the Routing section and tiers(name, cfg) under each provider card, and hands
// edits inside [data-roles] / [data-tiers] here. Only the draft changes: a suggestion is shown
// until the person picks it or presses Accept, and Save writes through /api/settings/ as ever.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ROLES = ["router", "planner", "reviewer", "chores"];                    // roles.ROLES
  const OFFABLE = ["reviewer", "chores"];                                       // roles.OFFABLE
  const TIERS = ["fast", "default", "top"];                                     // targets.TIERS
  const LABELS = { router: "Router", planner: "Planner", reviewer: "Reviewer", chores: "Chores" };
  const M = { got: null, err: "", rolesOpen: false, tiersOpen: false };
  const amber = (s) => (s ? `<div class="dim" style="color: var(--amber)">${esc(s)}</div>` : "");
  const opt = (v, cur, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
  const field = (label, html) => `<label class="set-field"><span>${label}</span>${html}</label>`;
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");

  async function load() {
    try {
      const r = await fetch("/api/models");
      const got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
      [M.got, M.err] = [got, ""];
    } catch (e) {
      M.err = "Can't read the model lists: " + e.message;
    }
  }

  // ---- roles, in the Routing tab -----------------------------------------------------------

  const roleOf = (d, r) => (d.roles && typeof d.roles === "object" && d.roles[r]) || "";

  // provider, provider:tier (with the model it picks) and each listed model, for every provider
  function choices() {
    const out = [];
    for (const [n, p] of Object.entries(M.got.providers)) {
      out.push([n, n]);
      if (!p.lists) continue;
      TIERS.forEach((t) => out.push([`${n}:${t}`, `${n}:${t}` + (p.tiers[t] ? ` (${p.tiers[t]})` : " (not picked)")]));
      (p.listed || []).forEach((m) => out.push([`${n}:${m.model}`, `${n}:${m.model}`]));
    }
    return out;
  }

  function picker(d, r, all) {
    const got = M.got.roles[r], cur = roleOf(d, r);
    const list = [["", `default (${got.default || "none"})`]].concat(OFFABLE.includes(r) ? [["off", "off"]] : [], all);
    if (cur && !list.some(([v]) => v === cur)) list.push([cur, cur]);
    const now = got.provider ? `${got.provider} → ${got.model || "its default"}` : "nobody";
    return field(esc(LABELS[r]), `<select class="set-in" data-role="${r}">${list.map(([v, l]) => opt(v, cur, l)).join("")}</select>
      <div class="dim">now ${esc(now)}</div>${amber(got.why && got.why !== "off" ? got.why : "")}`);
  }

  function roles(d) {
    if (!M.got) return `<div class="dim">${esc(M.err || "Reading the model lists…")}</div>`;
    const all = choices();
    const sum = ROLES.map((r) => `${r} ${roleOf(d, r) || M.got.roles[r].default || "none"}`).join(" · ");
    return `<div data-roles>${picker(d, "router", all)}
      <button type="button" class="ghost small" data-act="roles-open" title="eki's other jobs">${M.rolesOpen ? "▾" : "▸"} ${esc(sum)}</button>
      ${M.rolesOpen ? `<div class="set-grid">${ROLES.slice(1).map((r) => picker(d, r, all)).join("")}</div>` : ""}</div>`;
  }

  // one picker changed: set roles[role], or drop it (and an empty roles) for the default
  function editRole(el, d) {
    const r = el.dataset.role;
    if (!r) return false;
    const set = d.roles && typeof d.roles === "object" ? d.roles : {};
    if (el.value) set[r] = el.value; else delete set[r];
    if (Object.keys(set).length) d.roles = set; else delete d.roles;
    return true;
  }

  // ---- tiers, in the Providers tab ---------------------------------------------------------

  const picked = (cfg) => {
    const m = cfg.models && typeof cfg.models === "object" ? cfg.models : {};
    return Object.fromEntries(TIERS.map((t) => [t, m[t] || (t === "default" ? cfg.model : "") || ""]));
  };

  // "claude fast haiku · default sonnet · top opus; codex default gpt-…", from the draft
  function summary(draft) {
    if (!M.got) return `<div class="dim">${esc(M.err || "Reading the model lists…")}</div>`;
    const parts = Object.entries(M.got.providers).filter(([, p]) => p.lists).map(([n]) => {
      const t = picked((draft || {})[n] || {});
      const got = TIERS.filter((k) => t[k]).map((k) => `${k} ${t[k]}`).join(" · ");
      return `${n} ${got || "defaults"}`;
    });
    if (!parts.length) return "";
    return `<button type="button" class="ghost small" data-act="tiers-open" title="Pick a model per tier">${M.tiersOpen ? "▾" : "▸"} Models: ${esc(parts.join("; "))}</button>`;
  }

  function tiers(name, cfg) {
    const p = M.got && M.got.providers[name];
    if (!M.tiersOpen || !p || !p.lists) return "";
    const t = picked(cfg), listed = p.listed || [], sug = p.suggested || {};
    const row = (k) => {
      const ids = listed.map((m) => m.model);
      const list = [["", k === "default" ? "provider's default" : "not picked"]]
        .concat(listed.map((m) => [m.model, m.model + (m.label && m.label !== m.model ? ` — ${m.label}` : "") + (sug[k] === m.model ? " (suggested)" : "")]));
      if (t[k] && !ids.includes(t[k])) list.push([t[k], `${t[k]} (not in the list)`]);
      const take = sug[k] && sug[k] !== t[k]
        ? `<button type="button" class="ghost small" data-act="accept" data-tier="${k}" data-model="${esc(sug[k])}" title="Pick the suggestion">Accept ${esc(sug[k])}</button>` : "";
      return field(k, `<select class="set-in" data-tier="${k}">${list.map(([v, l]) => opt(v, t[k], l)).join("")}</select>${take}`);
    };
    return `<div data-tiers><div class="set-grid">${TIERS.map(row).join("")}</div>
      <div class="dim">${p.listed ? `${listed.length} models` : "list not read"}, read ${esc(when(p.read_at))}
        <button type="button" class="ghost small" data-act="mrefresh" data-name="${esc(name)}">Refresh</button></div></div>`;
  }

  function setTier(cfg, k, v) {
    const m = cfg.models && typeof cfg.models === "object" ? cfg.models : {};
    if (v) m[k] = v; else delete m[k];
    if (Object.keys(m).length) cfg.models = m; else delete cfg.models;
  }

  function editTier(el, cfg) { if (el.dataset.tier) setTier(cfg, el.dataset.tier, el.value); return true; }

  // a button of ours: true when the form should be drawn again
  async function act(a, btn, cfgOf, note) {
    if (a === "roles-open") return (M.rolesOpen = !M.rolesOpen), true;
    if (a === "tiers-open") return (M.tiersOpen = !M.tiersOpen), true;
    if (a === "accept") return setTier(cfgOf(), btn.dataset.tier, btn.dataset.model), true;
    if (a !== "mrefresh") return false;
    btn.disabled = true;
    const r = await fetch("/api/models/refresh", { method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify({ name: btn.dataset.name }) });
    const got = await r.json().catch(() => ({}));
    if (r.ok) M.got = got; else note("Not read: " + esc(got.error || r.statusText));
    return true;
  }

  window.settingsModels = { load, roles, editRole, summary, tiers, editTier, act };
})();
