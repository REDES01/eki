// One stage of a project's workflow in the right side panel (panel.host, panel.js), for the JSON of
// GET /api/projects/<pid>/workflow (eki/api_workflow.py): what the stage does, its settings with the
// workflow card's controls and one-line explanations (choicemenu.js for router / plan / merge, the
// settingstarget.js picker when router is off, a number for parallel, a switch per check, the command,
// the command as one line or path rules in rows (workflowrules.js) and batch_command, the playtester card,
// after_merge), then "Now here": the project's items at that stage, each opening its
// detail with a way back; one that needs a look (needs_look) says why in a line (its error, else its note) with
// Retry / Drop (POST /api/self/items/<id>/<action>, api_station.item_action; then reload()) and Open thread. Each change is saved at once into the workflow card (POST …/cards/<card>
// {"settings": …}; checks always the whole list in order), then reload() and redraw; what the server objects
// to shows beside its field, and nothing is redrawn under a box being typed in. Ticking command or batch
// with no command saved waits for the command; ticking batch on a rules card with no batch_command waits
// for that.
//   window.workflowInspect = { open(pid, data, stageId, reload), update(data), close(), current() }
(function () {
  if (document.querySelector && !document.querySelector('link[href="/ui/workflowinspect.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/workflowinspect.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const CHECKS = ["self", "review", "command", "batch", "playtester", "train"];   // eki/workflow.py CHECKS
  const LOCKED = "protects eki itself: the train, the drills and locked files can't be turned off here";
  const PARALLEL = 4, PLAYTESTER = "playtester";                                  // eki/components/workflow.py
  const RETRY = ["left", "unfit", "dropped", "rolled back"];                       // station.js RETRY
  const W = { pid: null, data: null, stage: null, reload: null, owner: null, el: null, item: null, itemData: null,
              pending: null, problems: {}, models: null, loading: false, last: "", cmd: null, acted: {} };
  const FIELDS = ["command", "paths", "batch_command"];                         // a "" problem naming one goes there

  function ago(t) {
    if (!t) return "";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    for (const [u, n] of [["d", 86400], ["h", 3600], ["m", 60]]) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
    return `${s}s ago`;
  }

  const D = () => W.data || {};
  const spec = () => D().spec || {};
  const locked = () => D().locked || [];
  const eki = () => !!D().eki || locked().length > 0;
  const stage = () => (D().stages || []).find((s) => s.id === W.stage) || null;
  const editable = () => !!(D().editable && D().card);
  const roWhy = () => (!D().card ? "no workflow card yet" : D().error || "the workflow card can't be changed here");
  const dis = () => (editable() ? "" : " disabled");
  const choice = (k) => (k in spec() ? spec()[k] : (D().choice_defaults || {})[k]);
  const saved = () => (Array.isArray(spec().checks) ? spec().checks : ["self"]);
  const ticked = () => { const on = W.pending || saved(); return CHECKS.filter((c) => c === "self" || locked().includes(c) || on.includes(c)); };
  const problem = (k) => (W.problems[k] ? `<div class="pj-err wi-err">${esc(W.problems[k])}</div>` : "");
  const field = (label, ctl, help, key) => `<div class="pp-field"><div class="pp-label">${esc(label)}</div><div class="pp-ctl">
    ${ctl}${help ? `<div class="dim wi-help">${esc(help)}</div>` : ""}${problem(key == null ? label : key)}</div></div>`;

  // ---- settings, per stage ------------------------------------------------------------------------

  function menu(k) {
    const cm = window.choiceMenu, cs = (D().choices || {})[k] || [], v = choice(k);
    return cm ? cm.html(k, cs, v, (D().choice_help || {})[k], !editable()) : `<code>${esc(v)}</code>`;
  }
  const helpOf = (k) => ((D().choice_help || {})[k] || {})[String(choice(k))] || "";

  function target() {
    if (choice("router") !== "off" && !W.problems.target) return "";
    if (!W.models && !W.loading) loadModels();
    const st = window.settingsTarget, cur = spec().target || "";
    const pick = st ? st.html(cur, { names: Object.keys(W.models || {}), models: W.models, extra: cur ? [] : [["", "pick one"]] })
      : `<code>${esc(JSON.stringify(cur))}</code>`;
    return `<div class="pp-field"><div class="pp-label">target</div><div class="pp-ctl wi-target"${editable() ? "" : ' data-off="1"'}>
      ${pick}<div class="dim wi-help">router off: everything goes here (provider → model → effort)</div>${problem("target")}</div></div>`;
  }

  function fixed(c) {
    if (c === "self") return "always on";
    if (locked().includes(c)) return LOCKED;
    if (c === "train" && !eki() && !saved().includes("train")) return "only eki's own project has the train";
    return "";
  }

  function toggle(c) {
    const on = ticked().includes(c), why = fixed(c);
    return field("on", `<label class="wi-switch${why ? " wi-fixed" : ""}"><input type="checkbox" data-wi-check="${c}"${on ? " checked" : ""}${why || !editable() ? " disabled" : ""}>
      <span class="wi-knob" aria-hidden="true"></span><span class="wi-state">${on ? "on" : "off"}</span></label>
      ${why ? `<div class="dim wi-lock">${c === "self" ? "" : "🔒 "}${esc(why)}</div>` : ""}`, "", "checks");
  }

  // what the ticked checks still need before they can be saved: "command", "batch_command" or ""
  function needs(list) {
    if (eki()) return "";
    const c = spec().command;
    if (list.some((x) => x === "command" || x === "batch") && !c) return "command";
    const b = spec().batch_command;
    return list.includes("batch") && Array.isArray(c) && !(typeof b === "string" && b.trim()) ? "batch_command" : "";
  }
  const cmdState = () => W.cmd || (W.cmd = window.workflowRules ? workflowRules.start(spec().command)
                                                               : { mode: "text", rows: [], text: spec().command || "" });

  function command() {
    const wait = W.pending && needs(W.pending) === "command" ? '<div class="dim wi-wait">type the project\'s check command to turn it on</div>' : "";
    const st = cmdState(), wr = window.workflowRules;
    const ctl = wr ? wr.html(st, { dis: dis(), eki: eki(), wait })
      : `<input class="set-in wi-text" data-wi="command" value="${esc(st.text)}" spellcheck="false"${dis()}>${wait}`;
    const help = eki() ? "eki's own check is bin/check, whatever is typed here"
      : st.mode === "rules" ? "each change runs every rule whose paths match a file it touched (.gitignore patterns: " +
        "design/**, *.md, tools/), each under its own heading; none matching: nothing to check"
      : "a shell command run in the change's folder; it must pass";
    return field("command", ctl + problem("paths"), help);
  }

  function batchCommand() {
    if (eki() || !window.workflowRules) return "";
    const wait = W.pending && needs(W.pending) === "batch_command"
      ? '<div class="dim wi-wait">type what runs on the changes together to turn batch on</div>' : "";
    return field("batch_command", workflowRules.batch(spec(), { dis: dis() }) + wait,
      Array.isArray(spec().command) ? "what runs on the queued changes together, on the stack's tip (e.g. the build)"
        : "what runs on the queued changes together; empty: the check command");
  }

  function settings(st) {
    const id = st.id, ro = editable() ? "" : `<div class="wi-ro">Read-only: ${esc(roWhy())}.</div>`;
    let h = "";
    if (id === "request") h = '<div class="dim">nothing to set: every request to this project starts here</div>';
    else if (id === "router" || id === "plan" || id === "merge") {
      h = field(id, menu(id), helpOf(id));
      if (id === "router") h += target();
      if (id === "merge" && choice("merge") !== "auto" && spec().after_merge) h += '<div class="dim">after_merge runs only with merge auto</div>';
    } else if (id === "build") {
      const n = spec().parallel == null ? PARALLEL : spec().parallel;
      h = field("parallel", `<input class="set-in wi-num" type="number" min="1" step="1" data-wi="parallel" value="${esc(n)}"${dis()}>`,
        "how many items build at once");
    } else if (CHECKS.includes(id)) {
      h = toggle(id);
      if (id === "command" || (id === "batch" && !spec().command)) h += command();
      if (id === "batch") h += batchCommand();
      if (id === "playtester") {
        h += field("playtester", `<input class="set-in wi-text" data-wi="playtester" value="${esc(spec().playtester || PLAYTESTER)}"
          spellcheck="false"${dis()}>`, "the project's playtester card that plays the change");
      }
    } else if (id === "after_merge") {
      h = field("after_merge", `<input class="set-in wi-text" data-wi="after_merge" value="${esc(spec().after_merge || "")}"
        placeholder="npm run build" spellcheck="false"${dis()}>`,
        "a shell command eki runs in your folder after it lands a change (merge auto), e.g. npm run build; empty: off");
      if (choice("merge") !== "auto") h += '<div class="dim">merge isn\'t auto: this doesn\'t run</div>';
    }
    return ro + h + problem("");
  }

  // ---- what's at the stage now --------------------------------------------------------------------

  const looks = (n) => `${n} ${n === 1 ? "needs" : "need"} a look`;
  const why = (it) => it.error || it.note || "no reason was written down";

  // why it needs a look in one line, and what to do about it
  function look(it) {
    if (!it.needs_look) return "";
    const btn = (a, label) => `<button class="wi-btn${a === "drop" ? " wi-drop" : ""}" data-wi-act="${a}" data-wi-id="${esc(it.id)}">${label}</button>`;
    const item = it.kind !== "goal";
    const thread = it.thread ? `<a class="wi-btn" href="#${encodeURIComponent(it.thread)}">Open thread</a>`
      : '<button class="wi-btn" disabled title="no thread yet">Open thread</button>';
    const said = W.acted[it.id] ? `<div class="pj-err wi-err">${esc(W.acted[it.id])}</div>` : "";
    return `<div class="wi-look"><span class="wi-why" title="${esc(why(it))}">${esc(why(it))}</span>
      <span class="wi-acts">${item && RETRY.includes(it.state) ? btn("retry", "Retry") : ""}${item ? btn("drop", "Drop") : ""}${thread}</span></div>${said}`;
  }

  function items(st) {
    const list = st.items || [];
    const n = st.alert_count || list.filter((i) => i.needs_look).length || 1;
    const alert = st.alert ? `<div class="wi-alert"><span class="wi-dot"></span>${esc(st.alert === true ? looks(n) : st.alert)}</div>` : "";
    const rows = list.map((it) => `<div class="wi-row${it.needs_look ? " wi-needs" : ""}"><a href="#" class="wi-item" data-wi-item="${esc(it.id)}">
      <span class="wi-title">${esc(it.title || it.id)}</span><span class="dim wi-meta">${esc(it.state || "")}${it.since ? " · " + esc(ago(it.since)) : ""}</span></a>${look(it)}</div>`);
    return `<h4 class="pp-h">Now here${list.length ? ` <span class="dim">${list.length}</span>` : ""}</h4>${alert}
      <div class="wi-items">${rows.join("") || '<div class="dim">nothing here now</div>'}</div>`;
  }

  function detail(st) {
    const it = W.itemData, row = (k, v) => (v ? `<div class="pp-field"><div class="pp-label">${k}</div><div class="pp-ctl">${v}</div></div>` : "");
    return `<a href="#" class="wi-back" data-wi-back="1">← back to ${esc(st.title)}</a>
      <h4 class="pp-h wi-dtitle">${esc(it.title || it.id)}</h4>
      ${row("state", esc(it.state || ""))}${row("since", esc(ago(it.since)))}${row("kind", esc(it.kind || ""))}
      ${row("where", it.where ? `<code>${esc(it.where)}</code>` : "")}${row("goal", esc(it.goal || ""))}
      ${it.error && it.error !== it.note ? `<div class="wi-note wi-error">${esc(it.error)}</div>` : ""}
      ${it.note ? `<div class="wi-note">${esc(it.note)}</div>` : ""}
      ${it.thread ? `<a class="wi-thread" href="#${encodeURIComponent(it.thread)}">open its thread →</a>` : ""}`;
  }

  function html() {
    const st = stage();
    if (!st) return '<div class="dim">this stage isn\'t in the workflow now</div>';
    if (W.item) {
      const fresh = (st.items || []).find((i) => i.id === W.item);
      if (fresh) W.itemData = fresh;
      if (W.itemData) return `<div class="wi">${detail(st)}</div>`;
    }
    const state = st.on ? "" : '<span class="wi-offtag">off · skipped</span>';
    const lock = st.locked && !CHECKS.includes(st.id) ? `<div class="dim wi-lock">🔒 ${esc(LOCKED)}</div>` : "";
    return `<div class="wi"><div class="wi-what">${esc(st.what || st.summary || "")} ${state}</div>${lock}
      <h4 class="pp-h">Settings</h4><div class="wi-settings">${settings(st)}</div>${items(st)}</div>`;
  }

  // drawn again only when it changed, and never under a box being typed in (except the one just saved)
  function paint(force, saving) {
    if (!W.el) return;
    const a = document.activeElement;
    const typing = a && W.el.contains && W.el.contains(a) && a.matches && a.matches("input:not([type=checkbox]), select, textarea");
    if (typing && !(force && (!saving || a === saving))) return;
    const h = html();
    if (force || h !== W.last) { W.el.innerHTML = h; W.last = h; }
    if (!editable()) for (const s of (W.el.querySelectorAll && W.el.querySelectorAll(".wi-target select, .wi-target input")) || []) s.disabled = true;
    const st = stage();
    if (st && window.panel && panel.retitle && panel.owner && panel.owner() === W.owner) panel.retitle(st.title, "Workflow");
  }

  async function loadModels() {
    W.loading = true;
    try { const r = await fetch("/api/models"); W.models = ((await r.json()) || {}).providers || {}; } catch (x) { W.models = {}; }
    W.loading = false;
    paint(true);
  }

  // ---- saving -------------------------------------------------------------------------------------

  async function save(set, el) {
    if (!editable()) return;
    const keys = Object.keys(set);
    for (const k of keys.concat("", "target", keys.includes("command") ? ["paths"] : [])) delete W.problems[k];
    const url = `/api/projects/${encodeURIComponent(W.pid)}/cards/${encodeURIComponent(D().card)}`;
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
                                   body: JSON.stringify({ settings: set }) });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) {
        const pr = got.problems || {};
        if (Object.keys(pr).length) Object.assign(W.problems, pr); else W.problems[keys[0]] = got.error || r.statusText || "not saved";
        const k = ((W.problems[""] || "").match(/^\w+/) || [""])[0];
        if (FIELDS.includes(k) && !W.problems[k]) { W.problems[k] = W.problems[""]; delete W.problems[""]; }
      } else {
        if (got.card && got.card.spec && W.data) W.data = Object.assign({}, W.data, { spec: got.card.spec });
        if (keys.includes("command")) W.cmd = null;                       // the rows again from what was saved
      }
    } catch (x) { W.problems[keys[0]] = x.message; }
    const pid = W.pid;
    if (W.reload) {
      try { const fresh = await W.reload(); if (fresh && W.pid === pid) W.data = fresh; } catch (x) { /* the next refresh brings it */ }
    }
    paint(true, el);
  }

  // Retry or Drop on an item that needs a look (Drop asks first), then the graph again
  async function act(a, id) {
    if (a === "drop" && !window.confirm(`Drop item ${id}?`)) return;
    delete W.acted[id];
    try {
      const r = await fetch(`/api/self/items/${encodeURIComponent(id)}/${a}`,
        { method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: "{}" });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) W.acted[id] = got.error || r.statusText || `couldn't ${a}`;
    } catch (x) { W.acted[id] = x.message; }
    const pid = W.pid;
    if (W.reload) {
      try { const fresh = await W.reload(); if (fresh && W.pid === pid) W.data = fresh; } catch (x) { /* the next refresh brings it */ }
    }
    paint(true);
  }

  // the checks waiting for this setting go with it once it's the one they wait for
  function pend(set) {
    if (W.pending && needs(W.pending) === Object.keys(set)[0]) { set.checks = W.pending; W.pending = null; }
    return set;
  }

  function onChange(e) {
    const t = e.target, ds = (t && t.dataset) || {};
    if (!editable()) return;
    if (ds.wiCheck) {
      const c = ds.wiCheck, now = ticked();
      const list = CHECKS.filter((x) => x === "self" || locked().includes(x) || (x === c ? t.checked : now.includes(x)));
      if (needs(list)) {
        W.pending = list;                                   // the server wants a command first: wait for it
        return paint(true);
      }
      W.pending = null;
      return save({ checks: list }, t);
    }
    if (ds.wi === "command") {
      cmdState().text = t.value;
      return save(pend({ command: t.value.trim() }), t);
    }
    if (ds.wrRow != null && window.workflowRules) {
      const set = workflowRules.change(cmdState(), ds, t.value);
      return set ? save(pend(set), t) : paint(true, t);
    }
    if (ds.wi === "batch_command") return save(pend({ batch_command: t.value.trim() || null }), t);
    if (ds.wi === "after_merge" || ds.wi === "playtester") return save({ [ds.wi]: t.value.trim() }, t);
    if (ds.wi === "parallel") return save({ parallel: t.value.trim() === "" ? null : Number(t.value) }, t);
    const wrap = t.closest && t.closest("[data-tgt]");
    if (wrap && window.settingsTarget) {
      const v = settingsTarget.value(wrap, t);
      if (!v) return;
      // "router: off" was refused for want of a target: the target picked turns it off
      return save(choice("router") === "off" ? { target: v } : { target: v, router: "off" }, t);
    }
  }

  function onClick(e) {
    const t = e.target, at = (sel) => t && t.closest && t.closest(sel);
    const doing = at("[data-wi-act]");
    if (doing) {
      if (e.preventDefault) e.preventDefault();
      return act(doing.dataset.wiAct, doing.dataset.wiId);
    }
    const item = at(".wi-item"), back = at(".wi-back"), wr = at("[data-wr-mode]") || at("[data-wr-add]") || at("[data-wr-del]");
    if (wr && window.workflowRules && editable()) {
      if (e.preventDefault) e.preventDefault();
      const set = workflowRules.click(cmdState(), wr.dataset);
      return set ? save(pend(set)) : paint(true);
    }
    if (item) {
      if (e.preventDefault) e.preventDefault();
      W.item = item.dataset.wiItem;
      W.itemData = ((stage() || {}).items || []).find((i) => i.id === W.item) || null;
      return paint(true);
    }
    if (back) {
      if (e.preventDefault) e.preventDefault();
      W.item = null; W.itemData = null;
      return paint(true);
    }
  }

  // ---- the interface ------------------------------------------------------------------------------

  function reset() {
    Object.assign(W, { el: null, owner: null, stage: null, item: null, itemData: null, pending: null, problems: {}, last: "", cmd: null, acted: {} });
  }

  function open(pid, data, stageId, reload) {
    const same = W.owner && W.pid === pid && W.stage === stageId;
    if (!same) { W.item = null; W.itemData = null; W.pending = null; W.problems = {}; W.cmd = null; }
    Object.assign(W, { pid, data, stage: stageId, reload: reload || null });
    if (same && W.el) return paint(true);
    const st = stage() || { title: stageId };
    const owner = {
      name: st.title, sub: "Workflow",
      draw(body) {
        body.innerHTML = "";
        const el = document.createElement("div");
        el.className = "wi-root";
        body.appendChild(el);
        el.addEventListener("change", onChange);
        el.addEventListener("click", onClick);
        if (window.choiceMenu) choiceMenu.attach(el, (key, value) => save({ [key]: value }));
        W.el = el; W.last = "";
        paint(true);
      },
      close() { if (W.owner === owner) reset(); },
    };
    W.owner = owner;
    panel.host(owner);
  }

  function update(data) {
    if (!W.owner || !data) return;
    W.data = data;
    paint(false);
  }

  function close() {
    const mine = W.owner && window.panel && panel.owner && panel.owner() === W.owner;
    reset();
    if (mine) panel.close();
  }

  window.workflowInspect = { open, update, close, current: () => (W.owner ? W.stage : null) };
})();
