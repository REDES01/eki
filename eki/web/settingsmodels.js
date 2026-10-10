// Settings: who eki's own jobs go to (routing.json "roles") and each provider's read model list,
// over GET /api/models (eki/api_models.py). It registers the "jobs" section with settings.js —
// roles(d), one grouped line per role, and the on-demand checker switch — and settingsproviders.js puts line(name) under
// each provider card. Each role's control is one target picker (settingstarget.js). Only the draft
// changes; Save writes through /api/settings/ as ever.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ROLES = ["router", "planner", "reviewer", "chores"];                    // roles.ROLES
  const OFFABLE = ["reviewer", "chores"];                                       // roles.OFFABLE
  const LABELS = { router: "Router", planner: "Planner", reviewer: "Reviewer", chores: "Chores" };
  const M = { got: null, err: "" };
  const amber = (s) => (s ? `<div class="set-amber">${esc(s)}</div>` : "");
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

  // /api/models' providers, or null before they are read: the row pickers in settings.js use it
  const models = () => (M.got ? M.got.providers : null);

  // ---- roles, in the Jobs section ---------------------------------------------------------

  const roleOf = (d, r) => (d.roles && typeof d.roles === "object" && d.roles[r]) || "";

  // one line per role: its name, who has it now (and why, when that's a fallback), its picker
  function picker(d, r) {
    const got = M.got.roles[r];
    const extra = [["", `default (${settingsTarget.key(got.default) || "none"})`]].concat(OFFABLE.includes(r) ? [["off", "off"]] : []);
    const tgt = settingsTarget.html(roleOf(d, r), { names: Object.keys(M.got.providers), models: M.got.providers, extra, attrs: `data-role="${r}"` });
    const now = got.provider ? "now " + (got.shown || `${got.provider} · ${got.model || "its default"}`) : "nobody";
    return settingsForm.line(esc(LABELS[r]), tgt, esc(now) + amber(got.why && got.why !== "off" ? got.why : ""));
  }

  function roles(d) {
    if (!M.got) return `<div class="dim">${esc(M.err || "Reading the model lists…")}</div>`;
    // four lines, always shown: each of eki's jobs is a thing you set, not a detail to unfold
    return `<div data-roles>${settingsForm.group("Who does eki's jobs", ROLES.map((r) => picker(d, r)).join(""))}</div>`;
  }

  // one picker changed: set roles[role], or drop it (and an empty roles) for the default.
  // True when the form should be drawn again (a select changed; typing doesn't redraw).
  function editRole(el, d) {
    const w = el.closest("[data-tgt]"), r = w && w.dataset.role;
    if (!r) return false;
    const v = settingsTarget.value(w, el);
    const set = d.roles && typeof d.roles === "object" ? d.roles : {};
    if (v) set[r] = v; else delete set[r];
    if (Object.keys(set).length) d.roles = set; else delete d.roles;
    return el.tagName === "SELECT";
  }

  // ---- each provider's list, in the Providers section---------------------------------------

  function line(name) {
    const p = M.got && M.got.providers[name];
    if (!p || !p.lists) return "";
    return `<div class="dim">${p.listed ? `${p.listed.length} models` : "list not read"}, read ${esc(when(p.read_at))} ·
      <button type="button" class="ghost small" data-act="mrefresh" data-name="${esc(name)}">Refresh</button></div>`;
  }

  // a button of ours: true when the form should be drawn again
  async function act(a, btn, note) {
    if (a !== "mrefresh") return false;
    btn.disabled = true;
    const r = await fetch("/api/models/refresh", { method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify({ name: btn.dataset.name }) });
    const got = await r.json().catch(() => ({}));
    if (r.ok) M.got = got; else note("Not read: " + esc(got.error || r.statusText));
    return true;
  }

  window.settingsModels = { load, models, roles, editRole, line, act };

  // the Jobs section: who each of eki's jobs goes to, and whether the prompt check may wake a model
  const jobs = {
    title: "Jobs", file: "routing",
    html: (d) => `<section class="set-sect">${settingsModels.roles(d)}${settingsForm.group("The prompt check",
      settingsForm.line("Wake an on-demand checker model for the prompt check",
        settingsForm.check(d.checker_wakes !== false, 'data-f="checker_wakes"', "")))}</section>`,
    edit(el, d) {
      if (el.dataset.f === "checker_wakes") { d.checker_wakes = el.checked; return false; }
      return el.closest("[data-roles]") ? editRole(el, d) : false;
    },
    act: () => false,   // Refresh (mrefresh) is settings.js's, from any section
  };
  if (window.settings) settings.section("jobs", jobs);
  else (window.settingsSections = window.settingsSections || []).push(["jobs", jobs]);
})();
