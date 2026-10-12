// The workflow card's own part in an open card (projectcardpanel.js draws it under "Workflow"): one line
// saying what happens to a request — the same words as the server's "line" (eki/api_workflow.py line) —
// and under it what parallel means (card.field_help: only this project's ceiling, the machine's can be lower), then what the checks run, "Each change: …" and "Together, before merging: …" (eki/workflow.py each_said,
// together_said, word for word), and "Open workflow →" to the project's Workflow page (#project/<pid>/workflow), where each stage is
// inspected and its settings changed. Every workflow setting is in `keys`, so the panel's form draws
// none of them and leaves no Settings section (form: false).
// Under the line, "How work lands" (GET/POST /api/projects/<id>/lands, eki/projectlands.py): "eki merges here"
// or "eki opens pull requests on GitHub". A folder with no GitHub remote opens a small form instead — name the
// repository, or create one when gh is signed in. The answer redraws the choice and the line's merge (the
// server sets the card's merge the same way: pr for GitHub, auto again for here when it was pr).
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  if (document.querySelector && !document.querySelector('link[href="/ui/cardworkflow.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardworkflow.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const KEYS = ["router", "target", "plan", "parallel", "checks", "command", "batch_command", "merge", "playtester", "after_merge", "access"];

  // a target as eki/targets.py key writes it: provider:model@effort
  function target(t) {
    if (!t) return "";
    if (typeof t !== "object") return String(t);
    const part = t.tier || t.model, out = part ? `${t.provider}:${part}` : String(t.provider || "");
    return t.effort ? `${out}@${t.effort}` : out;
  }

  // "plan split · 4 parallel · checks: self · merge auto", "router off → <target> · " first when off
  function line(spec) {
    const s = spec || {};
    const checks = (Array.isArray(s.checks) ? s.checks.filter((c) => typeof c === "string") : []);
    const head = (s.router || "on") === "off" ? `router off → ${target(s.target)} · ` : "";
    return `${head}plan ${s.plan || "split"} · ${s.parallel == null ? 4 : s.parallel} parallel · ` +
      `checks: ${(checks.length ? checks : ["self"]).join(", ")} · merge ${s.merge || "auto"}`;
  }

  const text = (v) => (typeof v === "string" ? v : "");
  const checksOf = (s) => (Array.isArray(s.checks) ? s.checks : ["self"]);
  // a rule's label: its name, else its paths joined (eki/pathrules.py label)
  const label = (r) => String(r.name || "").trim() || (r.paths || []).map((p) => String(p).trim()).join(", ");

  // what a rule runs (eki/workflow.py rule_runs): its test patterns, "whole suite" for "all", or its command
  const runs = (r) => (r.tests === "all" || (Array.isArray(r.tests) && r.tests.length === 1 && r.tests[0] === "all") ? "whole suite"
    : Array.isArray(r.tests) && r.tests.length ? r.tests.join(", ") : r.run);
  // "Each change: story files → npm run check:story · core → whole suite · …", or "Each change: <command>"
  function each(spec) {
    const s = spec || {}, cmd = s.command;
    if (Array.isArray(cmd) && cmd.length) return "Each change: " + cmd.map((r) => `${label(r || {})} → ${runs(r || {})}`).join(" · ");
    if (text(cmd)) return `Each change: ${cmd}`;
    return checksOf(s).includes("train") ? "Each change: bin/check" : "Each change: no check command";
  }

  // "Together, before merging: <batch_command, else a text command>", or nothing when batch is off
  function together(spec) {
    const s = spec || {};
    if (!checksOf(s).includes("batch")) return "Together, before merging: nothing (batch is off)";
    const b = text(s.batch_command).trim() ? s.batch_command : text(s.command);
    return `Together, before merging: ${b || "bin/check"}`;
  }

  // how work lands, per open card: the GET's answer, the small GitHub form, the API's error
  const L = { el: null, pid: null, card: null, ro: false, got: null, note: "", err: "", form: false, busy: false, last: "" };
  const landsUrl = () => "/api/projects/" + encodeURIComponent(L.pid) + "/lands";

  async function api(body) {
    const r = await fetch(landsUrl(), body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok || got.error) throw new Error(got.error || r.statusText);
    return got;
  }

  async function load(pid) {
    try { const got = await api(); if (L.pid === pid) L.got = got; }
    catch (x) { if (L.pid === pid) L.note = x.message; }
    if (L.pid === pid) paint();
  }

  async function choose(body) {
    if (L.busy) return;
    Object.assign(L, { busy: true, err: "" });
    paint();
    try {
      const got = await api(body);
      const s = L.card && L.card.spec;
      if (s) s.merge = got.lands === "github" ? "pr" : (s.merge === "pr" ? "auto" : s.merge);
      Object.assign(L, { got, form: false });
    } catch (x) { L.err = x.message; }
    L.busy = false;
    paint();
  }

  function landsPart() {
    const g = L.got;
    if (!g) return `<div class="cw-lands"><div class="cw-lands-h">How work lands</div>` +
      `<div class="cw-note">${esc(L.note || "…")}</div></div>`;
    const off = L.ro || L.busy, gh = g.lands === "github", ghOff = off || (!gh && !g.can_github);
    const opt = (value, words, on, dis, extra) =>
      `<button type="button" class="cw-opt${on ? " on" : ""}" data-cw-lands="${value}" aria-pressed="${on}"` +
      `${dis ? " disabled" : ""}>${esc(words)}${extra || ""}</button>`;
    const repo = gh && (g.repo || g.remote) ? ` <span class="cw-repo">${esc(g.repo || g.remote)}</span>` : "";
    let form = "";
    if (L.form && !gh) {
      form = `<form class="cw-gh"><label class="cw-gh-h">Add the GitHub repository ` +
        `<input class="cw-gh-repo" placeholder="OWNER/REPO or URL" autocomplete="off" spellcheck="false"></label>` +
        `<button type="submit" class="pj-btn" data-cw-act="add"${off ? " disabled" : ""}>Add</button>` +
        (g.signed_in ? `<div class="cw-gh-or">or</div><button type="button" class="pj-btn" data-cw-act="create"` +
          `${off ? " disabled" : ""}>Create one on GitHub</button> <label class="cw-gh-private">` +
          `<input type="checkbox" class="cw-gh-priv" checked> Private</label>` : "") +
        ` <button type="button" class="pj-btn" data-cw-act="cancel">Cancel</button></form>`;
    }
    return `<div class="cw-lands"><div class="cw-lands-h">How work lands</div><div class="cw-opts">` +
      opt("here", "eki merges here", !gh, off) + opt("github", "eki opens pull requests on GitHub", gh, ghOff, repo) +
      `</div>${!gh && !g.can_github && g.why ? `<div class="cw-why">${esc(g.why)}</div>` : ""}${form}` +
      `${L.err ? `<div class="pj-err cw-err">${esc(L.err)}</div>` : ""}</div>`;
  }

  // the whole part, written only when it changed (a half-typed repository survives the panel's redraws)
  function paint() {
    const el = L.el, card = L.card;
    if (!el || !card) return;
    const link = `<a class="cw-open" href="#project/${encodeURIComponent(L.pid)}/workflow">Open workflow →</a>`;
    const s = card.spec;
    const ceiling = (card.field_help || {}).parallel;       // eki/components/workflow.py FIELD_HELP
    const help = ceiling ? `<div class="dim cw-help">parallel: ${esc(ceiling)}</div>` : "";
    const html = `<div class="cw">${s ? `<div class="cw-line">${esc(line(s))}</div>${help}${landsPart()}` +
      `<div class="cw-each">${esc(each(s))}</div><div class="cw-together">${esc(together(s))}</div>` : ""}${link}</div>`;
    if (html !== L.last) { el.innerHTML = html; L.last = html; }
  }

  function onClick(e) {
    const t = e.target, b = t && t.closest && t.closest("[data-cw-lands], [data-cw-act]");
    if (!b || b.disabled) return;
    if (e.type === "submit") e.preventDefault();
    const g = L.got || {}, v = b.dataset.cwLands, act = b.dataset.cwAct;
    if (v === "here") return g.lands === "here" ? null : choose({ lands: "here" });
    if (v === "github") {
      if (g.lands === "github") return;
      if (g.remote) return choose({ lands: "github" });
      Object.assign(L, { form: true, err: "" });
      return paint();
    }
    if (act === "cancel") { Object.assign(L, { form: false, err: "" }); return paint(); }
    if (act === "add") {
      const repo = String((L.el.querySelector(".cw-gh-repo") || {}).value || "").trim();
      if (!repo) { L.err = "name the repository: OWNER/REPO or its URL"; return paint(); }
      return choose({ lands: "github", repo });
    }
    if (act === "create") {
      const box = L.el.querySelector(".cw-gh-priv");
      return choose({ lands: "github", create: true, private: box ? !!box.checked : true });
    }
  }
  // Enter in the repository field is Add
  function onSubmit(e) {
    if (!e.target || !e.target.matches || !e.target.matches(".cw-gh")) return;
    e.preventDefault();
    onClick({ type: "click", target: e.target.querySelector("[data-cw-act=add]") || e.target });
  }

  function draw(el, card, pid, ro, ctx) {
    const fresh = L.pid !== pid;
    if (fresh) Object.assign(L, { pid, got: null, note: "", err: "", form: false, busy: false });
    if (L.el !== el) L.last = "";
    Object.assign(L, { el, card, ro: !!ro || !!(ctx && ctx.editable === false) });
    if (el.addEventListener && !(el.dataset || {}).cwWired) {
      if (el.dataset) el.dataset.cwWired = "1";
      el.addEventListener("click", onClick);
      el.addEventListener("submit", onSubmit);
    }
    paint();
    if (fresh && card.spec) load(pid);
  }

  function close() { Object.assign(L, { el: null, pid: null, card: null, got: null, last: "", form: false }); }

  window.projectCardKinds.workflow = { title: "Workflow", keys: KEYS, form: false, draw, close, line, each, together };
})();
