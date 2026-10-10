// The mcp card's own part in an open card (projectcardpanel.js draws it under "Server"): the server
// (command and args, or url; env keys with their values masked as they reach the page) and a Test
// button that starts it for a quick handshake: "starts — N tools (1.2 s)" or the error, then its
// tools. POST /api/projects/<id>/cards/<name>/test (eki/api_cardmcp.py) — nothing is stored. The
// last result stays while the panel is open: the 5 s reload redraws but never tests again.
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
    const html = `<div class="cm-server">${serverPart((S.card.spec || {}).server)}</div>
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

  window.projectCardKinds = window.projectCardKinds || {};
  window.projectCardKinds["mcp"] = {
    title: "Server",
    draw(el, card, pid, ro) {
      if (!el) return;
      const key = pid + "/" + card.name;
      if (key !== S.key) Object.assign(S, { key, testing: false, result: null, err: "" });
      Object.assign(S, { box: el, card, pid });
      if (!el.dataset.cmWired) { el.dataset.cmWired = "1"; el.addEventListener("click", onClick); }
      paint();
    },
    close() { Object.assign(S, { key: "", box: null, card: null, pid: null, testing: false, result: null, err: "" }); },
  };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
