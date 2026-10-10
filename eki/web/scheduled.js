// Scheduled: #scheduled takes #main's place with what runs on its own — your scheduled prompts
// (eki/schedule.py) and the standing goals (eki/standing.py), grouped by project with eki first.
// Everything comes from GET /api/schedule (eki/api_schedule.py); each button POSTs the function
// the CLI calls and shows the server's error beside it. A row is one line; a click opens the rest.
// The page reloads every 5 s while shown and keeps nothing but which rows are open.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { on: false, timer: null, data: null, open: new Set(), err: "", errs: {} };
  const STYLE = `#scheduled-view .sc-row{border-bottom:1px solid var(--line);padding:10px 2px;cursor:pointer}
    #scheduled-view .sc-line{display:flex;align-items:center;gap:10px;min-width:0}
    #scheduled-view .sc-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #scheduled-view .sc-row.open .sc-text{white-space:pre-wrap}
    #scheduled-view .sc-more{display:none;margin-top:6px}
    #scheduled-view .sc-row.open .sc-more{display:block}
    #scheduled-view .sc-acts{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
    #scheduled-view .sc-acts .btn{padding:5px 10px;font-size:12.5px}
    #scheduled-view .sc-state{font-size:12px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--dim)}
    #scheduled-view .sc-state.stuck{color:var(--red)}
    #scheduled-view h2{font-size:15px;font-weight:600;margin:26px 0 6px}
    #scheduled-view h4{font-size:13px;font-weight:500;color:var(--dim);margin:14px 0 2px}
    #scheduled-view .sc-err{color:var(--red);font-size:13px}`;

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  function when(t) {
    if (!t) return "";
    const d = new Date(t * 1000), now = new Date();
    const same = d.toDateString() === now.toDateString();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return same ? `today ${time}` : `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${time}`;
  }

  const btn = (kind, id, verb, label, danger) =>
    `<button type="button" class="btn${danger ? " danger" : ""}" data-kind="${kind}" data-id="${esc(id)}" data-verb="${verb}">${label}</button>`;
  const err = (key) => (S.errs[key] ? `<span class="sc-err">${esc(S.errs[key])}</span>` : "");
  const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

  // ---- what it shows --------------------------------------------------------------------------

  function schedRow(s) {
    const key = "schedule:" + s.id;
    const open = S.open.has(key);
    const next = s.state === "on" && s.next_at ? `next ${esc(when(s.next_at))}` : esc(s.state);
    const last = s.last_at
      ? `last ${s.last_thread ? `<a href="#${encodeURIComponent(s.last_thread)}">${esc(when(s.last_at))}</a>` : esc(when(s.last_at))}`
      : "not run yet";
    const acts = [s.state === "paused" ? btn("schedule", s.id, "resume", "Resume") : btn("schedule", s.id, "pause", "Pause"),
      btn("schedule", s.id, "run", "Run now"), btn("schedule", s.id, "drop", "Drop", true)].join("");
    return `<div class="sc-row${open ? " open" : ""}" data-fold="${esc(key)}">
      <div class="sc-line"><span class="sc-text">${esc(s.text)}</span>
        <span class="quiet">${esc(s.cadence_text)}</span>${s.state !== "on" ? `<span class="sc-state">${esc(s.state)}</span>` : ""}</div>
      <div class="quiet">${esc(s.project_name || "No project")} · ${next} · ${last}${s.why ? ` · ${esc(s.why)}` : ""}</div>
      <div class="sc-more">
        <div class="quiet">${esc(s.target_label || "Auto")} · ${esc(s.tz)}</div>
        <div class="sc-acts">${acts}${err(key)}</div></div></div>`;
  }

  function goalRow(g) {
    const key = "standing:" + g.id;
    const open = S.open.has(key);
    const acts = [g.state === "paused" ? btn("standing", g.id, "resume", "Resume") : btn("standing", g.id, "pause", "Pause"),
      btn("standing", g.id, "drop", "Drop", true), btn("standing", g.id, "now", "Run now")].join("");
    const last = g.last_round_at ? ` · last round ${esc(when(g.last_round_at))}` : "";
    return `<div class="sc-row${open ? " open" : ""}" data-fold="${esc(key)}">
      <div class="sc-line"><span class="sc-text">${esc(g.text)}</span>
        <span class="sc-state ${esc(g.state)}">${esc(g.state)}</span></div>
      <div class="quiet">${plural(g.rounds || 0, "round")}${last}${g.why ? ` · ${esc(g.why)}` : ""}</div>
      <div class="sc-more"><div class="sc-acts">${acts}${err(key)}</div></div></div>`;
  }

  // standing goals by project: eki first, then by name, those with no project last
  function groups(standing) {
    const by = new Map();
    for (const g of standing.filter((x) => x.state !== "dropped")) {
      const k = g.project || "";
      if (!by.has(k)) by.set(k, { id: k, name: g.project_name || g.project || "No project", goals: [] });
      by.get(k).goals.push(g);
    }
    const rank = (x) => (x.id === "eki" ? 0 : x.id ? 1 : 2);
    return [...by.values()].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }

  // the page's body from /api/schedule's answer; pure, so tests can call it
  function render(d) {
    const scheds = (d.schedules || []).filter((s) => s.state !== "dropped");
    const gs = groups(d.standing || []);
    return `<h2>Scheduled prompts</h2>
      ${scheds.map(schedRow).join("") || '<div class="quiet">Nothing scheduled yet. “New scheduled task” runs a prompt hourly, daily, on weekdays or weekly.</div>'}
      <h2>Standing goals</h2>
      ${gs.map((g) => `<h4>${esc(g.name)}</h4>${g.goals.map(goalRow).join("")}`).join("") ||
        '<div class="quiet">No standing goals. <code>eki goal add</code> adds one.</div>'}`;
  }

  function box() {
    let el = $("scheduled-view");
    if (el) return el;
    el = document.createElement("div");
    el.id = "scheduled-view";
    el.innerHTML = `<style>${STYLE}</style><div class="page">
      <div class="page-head"><h1 class="page-title">Scheduled</h1>
        <div class="actions"><button type="button" class="btn-primary" id="sc-new">New scheduled task</button></div></div>
      <div id="sc-err" class="sc-err"></div>
      <div id="sc-body"><div class="quiet">Loading…</div></div></div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", onClick);
    return el;
  }

  function paint() {
    const body = $("sc-body");
    if (!body || !S.data) return;
    const html = render(S.data);
    if (body.innerHTML !== html) body.innerHTML = html;
    $("sc-err").textContent = S.err;
  }

  async function load() {
    try {
      S.data = await api("/api/schedule");
      S.err = "";
    } catch (e) { S.err = "Can't load the schedule: " + e.message; }
    if (!S.on) return;
    const n = S.data ? (S.data.schedules || []).filter((s) => s.state !== "dropped").length : 0;
    $("meta").textContent = S.data ? `${plural(n, "scheduled prompt")} · times in ${S.data.tz || "local time"}` : "";
    paint();
  }

  // ---- what it does ---------------------------------------------------------------------------

  // kind: "schedule" or "standing"; verb: pause | resume | drop | run (schedule) | now (standing)
  async function act(kind, id, verb) {
    if (verb === "drop" && !confirm(kind === "schedule" ? "Drop this scheduled prompt? It won't run again." :
      "Drop this standing goal? It stops for good.")) return false;
    const key = kind + ":" + id;
    delete S.errs[key];
    try {
      await api(`/api/${kind}/${encodeURIComponent(id)}/${verb}`, {});
    } catch (x) { S.errs[key] = x.message; paint(); return false; }
    await load();
    return true;
  }

  async function onClick(e) {
    if (e.target.closest("#sc-new")) {
      scheduleForm.open({ tz: S.data && S.data.tz, done: load });
      return;
    }
    const b = e.target.closest("[data-verb]");
    if (b) {
      b.disabled = true;
      await act(b.dataset.kind, b.dataset.id, b.dataset.verb);
      b.disabled = false;
      return;
    }
    if (e.target.closest("a")) return;
    const row = e.target.closest("[data-fold]");
    if (!row) return;
    const k = row.dataset.fold;
    S.open.has(k) ? S.open.delete(k) : S.open.add(k);
    row.classList.toggle("open", S.open.has(k));
  }

  // ---- the view -------------------------------------------------------------------------------

  function show() {
    const el = box();
    el.hidden = false;
    S.on = true;
    $("title").textContent = "Scheduled";
    $("meta").textContent = "";
    load();
    clearInterval(S.timer);
    S.timer = setInterval(load, 5000);
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    clearInterval(S.timer);
    box().hidden = true;
  }

  window.scheduled = { show, hide, render, act, groups };
  nav.register("scheduled", { show, hide });
})();
