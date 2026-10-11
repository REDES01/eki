// The workflow card's own part in an open card (projectcardpanel.js draws it under "Workflow"): one line
// saying what happens to a request — the same words as the server's "line" (eki/api_workflow.py line) —
// and "Open workflow →" to the project's Workflow page (#project/<pid>/workflow), where each stage is
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
  const KEYS = ["router", "target", "plan", "parallel", "checks", "command", "merge", "playtester", "after_merge", "access"];

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

  function draw(el, card, pid) {
    const link = `<a class="cw-open" href="#project/${encodeURIComponent(pid)}/workflow">Open workflow →</a>`;
    el.innerHTML = `<div class="cw">${card.spec ? `<div class="cw-line">${esc(line(card.spec))}</div>` : ""}${link}</div>`;
  }

  function close() {}

  window.projectCardKinds.workflow = { title: "Workflow", keys: KEYS, form: false, draw, close, line };
})();
