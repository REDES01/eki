// #project/<id>/workflow: one project's workflow as a graph — "<name> · Workflow", the card's one line,
// "← <name>" back to the project, and a dark canvas (workflowcanvas.js) of its stages and the cards that
// feed them. A stage click opens it in the side panel (workflowinspect.js); a card's side node opens that
// card there as the Cards grid does (projectcardpanel.js). With no workflow card the canvas shows the
// defaults under a banner that adds one. Everything comes from GET /api/projects/<id>/workflow; it reloads
// every 4 s while shown and keeps nothing.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { on: false, pid: null, data: null, timer: null, canvas: null, owner: null, err: "", fitted: false, last: {} };
  const C = () => window.workflowCanvas, I = () => window.workflowInspect;

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }
  const base = () => "/api/projects/" + encodeURIComponent(S.pid);

  // set a part only when its html changed
  function put(id, html) {
    const el = $(id);
    if (el && S.last[id] !== html) { el.innerHTML = html; S.last[id] = html; }
  }

  function box() {
    let el = $("workflow-view");
    if (el) return el;
    el = document.createElement("div");
    el.id = "workflow-view";
    el.innerHTML = `<div class="wf-bar"><a id="wf-back" class="wf-back" href="#"></a></div>
      <div id="wf-note"></div>
      <div id="wf-canvas" class="wf-canvas"></div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", onClick);
    return el;
  }

  // ---- the page ----------------------------------------------------------------------------------

  function note(d) {
    const parts = [];
    if (d && d.card === "") parts.push(`<div class="wf-banner"><span class="grow">No workflow card: these are the defaults.</span>
      <button type="button" class="pj-btn primary" data-wf-add>+ Add workflow card</button></div>`);
    if (d && d.error) parts.push(`<div class="wf-banner wf-broken"><b>${esc(d.card || "workflow")}</b> is broken: ${esc(d.error)}</div>`);
    if (S.err) parts.push(`<div class="pj-err wf-err">${esc(S.err)}</div>`);
    return parts.join("");
  }

  function header(d) {
    const name = (d && d.name) || S.pid;
    $("title").textContent = name + " · Workflow";
    $("meta").textContent = (d && d.line) || "";
    const back = $("wf-back");
    if (back) { back.href = "#project/" + encodeURIComponent(S.pid); back.textContent = "← " + name; }
    put("wf-note", note(d));
  }

  async function load() {
    const pid = S.pid;
    let d;
    try { d = await api(base() + "/workflow"); } catch (e) {
      if (S.on && S.pid === pid) { S.err = "Can't read the workflow: " + e.message; header(S.data); }
      return S.data;
    }
    if (!S.on || S.pid !== pid) return d;
    S.data = d; S.err = "";
    header(d);
    draw();
    if (I()) I().update(d);
    return d;
  }
  const reload = () => load();

  function draw(selected) {
    if (!S.canvas || !S.data) return;
    S.canvas.draw(S.data, selected !== undefined ? selected : I() ? I().current() : null);
    if (!S.fitted) { S.fitted = true; S.canvas.fit(); }
  }

  async function addCard(b) {
    if (b) b.disabled = true;
    try { await api(base() + "/cards", { template: "workflow" }); S.err = ""; } catch (e) { S.err = "Can't add the card: " + e.message; }
    S.last["wf-note"] = null;
    await load();
  }

  function onClick(e) {
    const add = e.target.closest && e.target.closest("[data-wf-add]");
    if (add) return addCard(add);
  }

  // ---- a node opens in the side panel -------------------------------------------------------------

  function stage(id) {
    if (!S.data || !I()) return;
    I().open(S.pid, S.data, id, reload);
    draw(id);
  }

  function feed(f) {
    if (!f) return;
    if (!f.card) return stage("command");      // the check command lives on the card: its stage
    return openCard(f.card);
  }

  // a script the card panel needs, loaded once
  function need(name, src) {
    if (window[name] || !document.querySelector) return Promise.resolve();
    let t = document.querySelector(`script[src="${src}"]`);
    if (!t) {
      t = document.createElement("script");
      t.src = src; t.async = false;
      document.head.appendChild(t);
    }
    return new Promise((ok) => { t.addEventListener("load", ok); t.addEventListener("error", ok); });
  }

  // "2 min ago" from epoch seconds (or an ISO string), as the Cards grid says it
  function ago(t) {
    if (t == null || t === "") return "";
    const ms = typeof t === "number" ? t * 1000 : Date.parse(t);
    if (isNaN(ms)) return String(t);
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  }

  const ours = () => !!(S.owner && window.panel && panel.owner() === S.owner);

  async function openCard(name) {
    if (!window.panel) return;
    const pid = S.pid;
    let cards;
    try { cards = await api(base() + "/cards"); } catch (e) { S.err = `Can't open ${name}: ${e.message}`; return header(S.data); }
    await need("projectCardPanel", "/ui/projectcardpanel.js");
    if (!S.on || S.pid !== pid) return;
    const card = (cards.cards || []).find((c) => c.name === name);
    if (!card) { S.err = `No card named ${name} in this project.`; return header(S.data); }
    const ro = cards.readonly || "";
    const o = {
      name: card.name, sub: (card.kind || "?") + (card.about ? " · " + card.about : ""),
      card,
      draw(body) {
        if (window.projectCardPanel) projectCardPanel.open(body, pid, o.card, ro, { api, ago, reload: () => fresh(o), close: () => panel.close() });
      },
      close() {
        window.projectCardPanel && projectCardPanel.close();
        if (S.owner === o) S.owner = null;
      },
    };
    S.owner = o;
    draw(null);
    panel.host(o);
  }

  // the open card changed: it as it is now, and the graph it feeds
  async function fresh(o) {
    try {
      const got = await api(base() + "/cards");
      const now = (got.cards || []).find((c) => c.name === o.name);
      if (now && S.owner === o && window.projectCardPanel) { o.card = now; projectCardPanel.update(now, got.readonly || ""); }
    } catch (e) { /* the next reload says so */ }
    return load();
  }

  // ---- the view -----------------------------------------------------------------------------------

  function show(pid) {
    const el = box();
    el.hidden = false;
    if (S.pid !== pid) { S.data = null; S.err = ""; }
    S.on = true; S.pid = pid; S.last = {}; S.fitted = false;
    header(S.data);
    if (S.canvas) S.canvas.unmount();
    S.canvas = C() ? C().mount($("wf-canvas"), { stage, feed }) : null;
    if (!S.canvas) put("wf-canvas", '<div class="wf-empty dim">The workflow graph is not in this build.</div>');
    load();
    clearInterval(S.timer);
    S.timer = setInterval(load, 4000);
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    clearInterval(S.timer);
    S.timer = null;
    if (I()) I().close();
    if (ours()) panel.close();
    S.owner = null;
    if (S.canvas) S.canvas.unmount();
    S.canvas = null;
    box().hidden = true;
  }

  if (document.querySelector && !document.querySelector('link[href="/ui/workflowpage.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/workflowpage.css";
    document.head.appendChild(l);
  }

  window.workflowPage = { show, hide, reload };
  nav.register("workflow", { show, hide });
})();
