// The mcp card's own part in an open card (projectcardpanel.js draws it under "Server"): the server
// (command and args, or url; env keys with their values masked as they reach the page) and a Test
// button that starts it for a quick handshake: "starts — N tools (1.2 s)" or the error, then its
// tools. POST /api/projects/<id>/cards/<name>/test (eki/api_cardmcp.py) — nothing is stored. The
// last result stays while the panel is open: the 5 s reload redraws but never tests again.
// A service card offering an MCP server (its "mcp" field) gets the same part under "Script and MCP":
// "script: <run, or call and serve, or provider>" and "MCP: <command or url>" with its env and Test.
// A service card without "mcp" has no such section: the part hides itself and its title.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { key: "", box: null, card: null, pid: null, testing: false, result: null, err: "" };

  if (document.querySelector && !document.querySelector('link[href="/ui/cardmcp.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardmcp.css";
    document.head.appendChild(l);
  }

  const testUrl = () => "/api/projects/" + encodeURIComponent(S.pid) + "/cards/" + encodeURIComponent(S.card.name) + "/test";

  function serverPart(s) {
    if (!s || typeof s !== "object") return '<div class="pj-err">No server set.</div>';
    const env = s.env && typeof s.env === "object" ? Object.keys(s.env) : [];
    const what = s.url ? `<div class="cm-line"><span class="dim">url</span> <code>${esc(s.url)}</code></div>`
      : `<div class="cm-line"><span class="dim">command</span> <code>${esc([s.command || ""].concat(s.args || []).join(" "))}</code></div>`;
    const envs = env.length ? `<div class="cm-line"><span class="dim">env</span> ${env.map((k) =>
      `<code>${esc(k)}=${esc(s.env[k])}</code>`).join(" ")}</div>` : "";
    return what + envs;
  }

  const argv = (v) => Array.isArray(v) ? v.join(" ") : String(v == null ? "" : v);

  // how `eki use <name>` calls a service: its run argv, its call argv on its server, or its provider
  function scriptOf(spec) {
    if (spec.run != null) return `<code>${esc(argv(spec.run))}</code>`;
    if (spec.serve != null || spec.call != null) {
      return (spec.call != null ? `<code>${esc(argv(spec.call))}</code> ` : "")
        + (spec.serve != null ? `<span class="dim">on the server</span> <code>${esc(argv(spec.serve))}</code>` : "");
    }
    if (spec.provider) return `<span class="dim">eki's</span> <code>${esc(spec.provider)}</code> <span class="dim">provider</span>`;
    return '<span class="dim">none set</span>';
  }

  function servicePart(spec) {
    const m = spec.mcp, env = m.env && typeof m.env === "object" ? Object.keys(m.env) : [];
    const what = m.url ? `<code>${esc(m.url)}</code>` : `<code>${esc([m.command || ""].concat(m.args || []).join(" "))}</code>`;
    return `<div class="cm-line cm-script"><span class="dim">script:</span> ${scriptOf(spec)}</div>
      <div class="cm-line cm-mcp"><span class="dim">MCP:</span> ${what}</div>`
      + (env.length ? `<div class="cm-line"><span class="dim">env</span> ${env.map((k) =>
        `<code>${esc(k)}=${esc(m.env[k])}</code>`).join(" ")}</div>` : "");
  }

  const hasMcp = (card) => !!(card && card.spec && card.spec.mcp && typeof card.spec.mcp === "object");

  function resultPart() {
    if (S.testing) return '<div class="dim">Starting it…</div>';
    if (S.err) return `<div class="pj-err">${esc(S.err)}</div>`;
    const r = S.result;
    if (!r) return '<div class="dim">Not tested yet: Test starts it and asks for its tools.</div>';
    const secs = `${Math.round(Number(r.seconds || 0) * 10) / 10} s`;
    if (!r.ok) return `<div class="pj-err">doesn't start: ${esc(r.error)} <span class="dim">(${secs})</span></div>`;
    const tools = r.tools || [], who = r.name ? ` <span class="dim">${esc(r.name)}${r.version ? " " + esc(r.version) : ""}</span>` : "";
    return `<div class="cm-ok">starts — ${tools.length} tool${tools.length === 1 ? "" : "s"} (${secs})${who}</div>`
      + tools.map((t) => `<div class="pj-item cm-tool"><b>${esc(t.name)}</b><span class="grow dim">${esc(t.description)}</span></div>`).join("");
  }

  function paint() {
    if (!S.box || !S.card) return;
    const spec = S.card.spec || {}, service = S.card.kind === "service";
    const head = S.box.previousElementSibling, off = service && !hasMcp(S.card);
    S.box.hidden = off;
    if (head && head.classList && head.classList.contains("pp-h")) head.hidden = off;
    if (off) { if (S.box._cmLast !== "") { S.box.innerHTML = ""; S.box._cmLast = ""; } return; }
    const html = `<div class="cm-server">${service ? servicePart(spec) : serverPart(spec.server)}</div>
      <div class="pj-row"><button type="button" class="pj-btn" data-cm-test="1"${S.testing ? " disabled" : ""}>Test</button></div>
      <div class="cm-result">${resultPart()}</div>`;
    if (S.box._cmLast !== html) { S.box.innerHTML = html; S.box._cmLast = html; }
  }

  async function test() {
    const key = S.key;
    S.testing = true; S.err = ""; paint();
    let got = null, err = "";
    try {
      const r = await fetch(testUrl(), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) err = d.error || `HTTP ${r.status}`; else got = d;
    } catch (x) { err = x.message || String(x); }
    if (S.key !== key) return;                       // another card opened meanwhile
    S.testing = false; S.result = got; S.err = err;
    paint();
  }

  function onClick(e) {
    if (e.target.closest && e.target.closest("[data-cm-test]") && !S.testing) test();
  }

  const part = (title, keys) => ({
    title, keys,
    draw(el, card, pid, ro) {
      if (!el) return;
      const key = pid + "/" + card.name;
      if (key !== S.key) Object.assign(S, { key, testing: false, result: null, err: "" });
      Object.assign(S, { box: el, card, pid });
      if (!el.dataset.cmWired) { el.dataset.cmWired = "1"; el.addEventListener("click", onClick); }
      paint();
    },
    close() { Object.assign(S, { key: "", box: null, card: null, pid: null, testing: false, result: null, err: "" }); },
  });
  window.projectCardKinds = window.projectCardKinds || {};
  window.projectCardKinds["mcp"] = part("Server", undefined);
  window.projectCardKinds["service"] = part("Script and MCP", ["mcp"]);
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
