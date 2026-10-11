// The workflow card's own part in an open card (projectcardpanel.js draws it under "Workflow"): one line
// saying what happens to a request — the same words as the server's "line" (eki/api_workflow.py line) —
// then what the checks run, "Each change: …" and "Together, before merging: …" (eki/workflow.py each_said,
// together_said, word for word), and "Open workflow →" to the project's Workflow page (#project/<pid>/workflow), where each stage is
// inspected and its settings changed. Every workflow setting is in `keys`, so the panel's form draws
// none of them and leaves no Settings section (form: false).
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

  // "Each change: story files → npm run check:story · code → …", or "Each change: <command>"
  function each(spec) {
    const s = spec || {}, cmd = s.command;
    if (Array.isArray(cmd) && cmd.length) return "Each change: " + cmd.map((r) => `${label(r || {})} → ${(r || {}).run}`).join(" · ");
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

  function draw(el, card, pid) {
    const link = `<a class="cw-open" href="#project/${encodeURIComponent(pid)}/workflow">Open workflow →</a>`;
    const s = card.spec;
    el.innerHTML = `<div class="cw">${s ? `<div class="cw-line">${esc(line(s))}</div>` +
      `<div class="cw-each">${esc(each(s))}</div><div class="cw-together">${esc(together(s))}</div>` : ""}${link}</div>`;
  }

  function close() {}

  window.projectCardKinds.workflow = { title: "Workflow", keys: KEYS, form: false, draw, close, line, each, together };
})();
