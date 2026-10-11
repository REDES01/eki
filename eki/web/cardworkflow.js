// The workflow card's own part in an open card (projectcardpanel.js draws it under "Workflow"; router,
// plan and merge stay dropdowns in its Settings): the checks as toggles in the order they run, each with
// one line on what it does — self always on; on eki's own project the card's `locked` checks
// (workflow.LOCKED, from eki/api_cards.py) shown on and fixed; train only for eki — then the check
// command when command or batch is on, the target picker (settingstarget.js) when router is off, and
// parallel. Each change is saved like any setting (ctx.save → POST …/cards/<name> {"settings": …}) and
// what the server objects to shows beside its field. Ticking command or batch with no command saved
// waits for the command, and picking a target after "router: off" was refused for want of one turns it off.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  if (document.querySelector && !document.querySelector('link[href="/ui/cardworkflow.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardworkflow.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const CHECKS = [                                         // eki/workflow.py CHECKS, in the order they run
    ["self", "the building agent verifies its own work — screenshots, its tests — before it finishes; always on"],
    ["review", "a second reader agent"],
    ["command", "the project's check command"],
    ["batch", "the queue checks queued changes together and bisects on red"],
    ["playtester", "the project's playtester card plays the change"],
    ["train", "build + drills + swap, only for eki itself"],
  ];
  const LOCKED = "protects eki itself: the train, the drills and locked files can't be turned off here";
  const PARALLEL = 4;                                      // eki/components/workflow.py PARALLEL
  const W = { el: null, card: null, ro: "", ctx: null, pending: null, models: null, loading: false, last: "" };

  const spec = () => W.card.spec || {};
  const locked = () => W.card.locked || [];
  const eki = () => locked().length > 0;
  const off = () => !(W.ctx && W.ctx.editable);
  const saved = () => (Array.isArray(spec().checks) ? spec().checks : ["self"]);
  // what is ticked: self and the locked checks always, a pending choice over the saved one
  const ticked = () => { const on = W.pending || saved(); return CHECKS.map(([c]) => c).filter((c) => c === "self" || locked().includes(c) || on.includes(c)); };
  const problem = (k) => { const p = (W.ctx && W.ctx.problems) || {}; return p[k] ? `<div class="pj-err">${esc(p[k])}</div>` : ""; };
  const router = () => spec().router || "on";
  const wantsCommand = () => ticked().some((c) => c === "command" || c === "batch");
  const showTarget = () => router() === "off" || !!((W.ctx && W.ctx.problems) || {}).target;

  function fixed(c) {
    if (c === "self") return "always on";
    if (locked().includes(c)) return LOCKED;
    if (c === "train" && !eki() && !saved().includes("train")) return "only eki's own project has the train";
    return "";
  }

  function checks() {
    const on = ticked();
    return CHECKS.map(([c, what]) => {
      const why = fixed(c), dis = off() || why ? " disabled" : "";
      return `<label class="cw-check${why ? " cw-fixed" : ""}"><input type="checkbox" data-cw-check="${c}"${on.includes(c) ? " checked" : ""}${dis}>
        <span class="cw-name">${c}</span><span class="dim cw-what">${esc(what)}${why && c !== "self" ? `<span class="cw-lock"> · ${esc(why)}</span>` : ""}</span></label>`;
    }).join("");
  }

  function command() {
    if (!wantsCommand()) return "";
    const wait = W.pending && !spec().command ? '<div class="dim">type the project\'s check command to turn it on</div>' : "";
    return `<div class="pp-field"><div class="pp-label">command</div><div class="pp-ctl">
      <input class="set-in cw-command" data-cw="command" value="${esc(spec().command || "")}" placeholder="${eki() ? "bin/check" : "the project's check command"}" spellcheck="false"${off() ? " disabled" : ""}>
      ${wait}${problem("command")}</div></div>`;
  }

  function target() {
    if (!showTarget()) return "";
    if (!W.models && !W.loading) loadModels();
    const st = window.settingsTarget, cur = spec().target || "";
    const pick = st ? st.html(cur, { names: Object.keys(W.models || {}), models: W.models, extra: cur ? [] : [["", "pick one"]] })
      : `<code>${esc(JSON.stringify(cur))}</code>`;
    return `<div class="pp-field"><div class="pp-label">target</div><div class="pp-ctl cw-target"${off() ? ' data-off="1"' : ""}>
      ${pick}<div class="dim">router off: everything goes here (provider → model → effort)</div>${problem("target")}</div></div>`;
  }

  function parallel() {
    const n = spec().parallel == null ? PARALLEL : spec().parallel;
    return `<div class="pp-field"><div class="pp-label">parallel</div><div class="pp-ctl">
      <input class="set-in cw-parallel" type="number" min="1" step="1" data-cw="parallel" value="${esc(n)}"${off() ? " disabled" : ""}>
      <span class="dim">how many items build at once</span>${problem("parallel")}</div></div>`;
  }

  function html() {
    return `<div class="cw"><div class="cw-checks">${checks()}</div>${problem("checks")}${problem("")}
      ${command()}${target()}${parallel()}</div>`;
  }

  async function loadModels() {
    W.loading = true;
    try { const r = await fetch("/api/models"); W.models = ((await r.json()) || {}).providers || {}; } catch (x) { W.models = {}; }
    W.loading = false;
    if (W.el) paint(true);
  }

  // drawn again only when it changed and never under the box you're typing in
  function paint(force) {
    const a = document.activeElement;
    if (!force && a && W.el.contains && W.el.contains(a) && a.matches && a.matches("input:not([type=checkbox]), select")) return;
    const h = html();
    if (force || h !== W.last) { W.el.innerHTML = h; W.last = h; }
    if (off()) for (const s of (W.el.querySelectorAll && W.el.querySelectorAll(".cw-target select, .cw-target input")) || []) s.disabled = true;
  }

  // the panel draws this part again with the saved card; once more here in case the box still had the focus
  const save = (set) => Promise.resolve(W.ctx.save(set)).then(() => W.el && paint(true));

  function onChange(e) {
    const t = e.target;
    if (!W.card || off()) return;
    if (t.dataset && t.dataset.cwCheck) {
      const on = Array.from(W.el.querySelectorAll("[data-cw-check]")).filter((x) => x.checked).map((x) => x.dataset.cwCheck);
      const list = CHECKS.map(([c]) => c).filter((c) => c === "self" || locked().includes(c) || on.includes(c));
      if (list.some((c) => c === "command" || c === "batch") && !spec().command && !eki()) {
        W.pending = list;                       // the server wants a command first: wait for it
        return paint(true);
      }
      W.pending = null;
      return save({ checks: list });
    }
    if (t.dataset && t.dataset.cw === "command") {
      const set = { command: t.value.trim() };
      if (W.pending) set.checks = W.pending;
      W.pending = null;
      return save(set);
    }
    if (t.dataset && t.dataset.cw === "parallel") return save({ parallel: t.value.trim() === "" ? null : Number(t.value) });
    const wrap = t.closest && t.closest("[data-tgt]");
    if (wrap && window.settingsTarget) {
      const v = settingsTarget.value(wrap, t);
      if (!v) return;
      // "router: off" was refused for want of a target: the target picked turns it off
      return save(router() === "off" ? { target: v } : { target: v, router: "off" });
    }
  }

  function draw(el, card, pid, ro, ctx) {
    if (W.el !== el) { W.pending = null; W.last = ""; }
    if (W.card && W.card.name !== card.name) W.pending = null;
    Object.assign(W, { el, card, ro, ctx });
    if (!el.dataset.cwWired) {
      el.dataset.cwWired = "1";
      el.addEventListener("change", onChange);
    }
    paint(false);
  }

  function close() { Object.assign(W, { el: null, card: null, pending: null, last: "" }); }

  window.projectCardKinds.workflow = { title: "Workflow", keys: ["checks", "command", "target", "parallel"], draw, close, CHECKS };
})();
