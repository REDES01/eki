// The project page's Cards area (project.js mounts it into #pj-cards): the project's components as
// cards in a grid — name, kind (by its title: "Preview links", not serve), about line, state, last use, a × that moves it to the Trash — and a
// last "+ Add card" tile that opens the library below the grid (templates, then the pool's components).
// The header row's type filter (projectcardfilter.js) sorts the grid by kind then name and can narrow it.
// A click on a card opens it in the side panel (panel.host, drawn by projectcardpanel.js); the grid stays
// put with the open card marked. Everything comes from /api/projects/<id>/cards (eki/api_cards.py); it
// reloads every 5 s while mounted and keeps nothing else.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { el: null, pid: null, timer: null, data: null, lib: null, open: null, owner: null, err: "", last: {},
    filter: null };
  const PF = () => window.projectCardFilter;
  // the type filter for this project, made once its script is in
  function filter() {
    if (!S.filter && PF() && S.pid) S.filter = PF().state(S.pid);
    return S.filter;
  }

  async function api(path, body, method) {
    const r = await fetch(path, body === undefined && !method ? {} : {
      method: method || "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify(body || {}) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(got.error || r.statusText); e.problems = got.problems || {}; throw e; }
    return got;
  }
  const base = () => "/api/projects/" + encodeURIComponent(S.pid) + "/cards";
  const q = (sel) => S.el && S.el.querySelector(sel);

  // set a part only when its html changed, so what you are typing in it stays
  function put(sel, html) {
    const el = q(sel);
    if (el && S.last[sel] !== html) { el.innerHTML = html; S.last[sel] = html; }
  }

  // "2 min ago" from epoch seconds (or an ISO string)
  function ago(t) {
    if (t == null || t === "") return "";
    const ms = typeof t === "number" ? t * 1000 : Date.parse(t);
    if (isNaN(ms)) return String(t);
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
    return new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });
  }

  // an instructions card shows its file's first heading and length in place of the about line
  function about(c) {
    const f = c.kind === "instructions" && c.instructions;
    if (!f) return c.about ? `<div class="pc-about">${esc(c.about)}</div>` : "";
    if (!f.exists) return `<div class="pc-about dim">${esc(f.file)} missing</div>`;
    return `<div class="pc-about">${esc(f.heading || f.file)}</div><div class="dim">${esc(f.lines)} lines</div>`;
  }

  // a serve card's links that are up ("folder → URL", the folder cut to its last two parts) and
  // an assets card's pool size; nothing when there are none
  function serving(c) {
    const short = (f) => String(f || "").split("/").filter(Boolean).slice(-2).join("/");
    const links = (c.links || []).map((l) => `<div class="dim">${esc(short(l.folder))} → ` +
      `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.url)}</a></div>`).join("");
    return links + (c.size ? `<div class="dim">pool: ${esc(c.size)}</div>` : "");
  }

  function tile(c, ro) {
    const broken = !!c.error;
    const state = broken ? c.state || "broken" : c.state || "idle";
    const cls = broken ? "broken" : /^in use|waiting/.test(state) ? "busy" : state === "up" ? "up" : "";
    return `<div class="pc-tile${broken ? " pc-broken" : ""}${S.open === c.name ? " on" : ""}" data-card="${esc(c.name)}" role="button" tabindex="0">
      ${ro ? "" : `<button type="button" class="pc-x" data-rm="${esc(c.name)}" title="Remove (moves it to the Trash)">×</button>`}
      <div class="pc-name">${esc(c.name)}</div>
      <div class="pc-kind dim">${esc(c.title || c.kind || "?")}${c.ref ? " · from the pool" : ""}</div>
      ${about(c)}
      ${serving(c)}
      ${broken ? `<div class="pj-err pc-about">${esc(c.error)}</div>` : ""}
      <div class="pc-foot"><span class="pc-state ${cls}">${esc(state)}</span>
        <span class="dim">${c.last_use ? esc(ago(c.last_use)) : "never used"}</span></div></div>`;
  }

  function library() {
    const L = S.lib;
    if (!L) return "";
    if (L.loading) return `<div class="pj-card pc-lib dim">Loading the library…</div>`;
    const row = (x, attr) => `<button type="button" class="pj-item pc-pick" ${attr}="${esc(x.name)}">
        <span class="pc-pname">${esc(x.name)}</span><span class="dim">${esc(x.title || x.kind || "")}</span>
        <span class="grow">${x.error ? `<span class="pj-err">${esc(x.error)}</span>` : esc(x.about || "")}</span></button>`;
    return `<div class="pj-card pc-lib"><div class="pj-row"><b class="grow">Add a card</b>
        <button type="button" class="pj-btn" data-lib="close">Close</button></div>
      <div class="pj-sub">Templates — a new component in this project, yours to set up</div>
      ${(L.templates || []).map((t) => row(t, "data-tpl")).join("") || '<div class="dim">No templates.</div>'}
      <div class="pj-sub">From the pool — attached by reference, set up in Settings</div>
      ${(L.pool || []).map((p) => row(p, "data-pool")).join("") || '<div class="dim">Nothing in the pool (~/.eki/components/).</div>'}
      ${L.err ? `<div class="pj-err">${esc(L.err)}</div>` : ""}</div>`;
  }

  function draw() {
    const d = S.data;
    if (!d) return;
    const ro = d.readonly || "";
    const cards = d.cards || [];
    put(".pc-sum", cards.length ? `${cards.length} card${cards.length === 1 ? "" : "s"}` : "");
    const sg = !ro && d.suggest;
    put(".pc-suggest", sg ? `<div class="pj-card pj-row pc-suggest-row"><span class="grow">${esc(sg.why || "")}</span>
        <button type="button" class="pj-btn primary" data-suggest="${esc(sg.template)}">Add instructions card</button></div>` : "");
    const F = filter();
    put(".pc-filter", F && cards.length ? PF().html(F, cards) : "");
    put(".pc-grid", (F ? PF().shown(F, cards) : cards).map((c) => tile(c, ro)).join("") +
      (ro ? "" : `<div class="pc-tile pc-add" data-lib="open" role="button" tabindex="0">+ Add card</div>`));
    put(".pc-note", (ro ? `<div class="dim">Cards are read-only here: ${esc(ro)}.</div>` : "") +
      (S.err ? `<div class="pj-err">${esc(S.err)}</div>` : ""));
    put(".pc-libbox", ro ? "" : library());
    const open = cards.find((c) => c.name === S.open);
    if (S.open && !open) closeCard();
    else if (open && ours()) {
      if (window.projectCardPanel) projectCardPanel.update(open, ro);
      const sub = subOf(open);
      if (sub !== S.owner.sub) { S.owner.sub = sub; panel.retitle(open.name, sub); }
    }
  }

  async function load() {
    const pid = S.pid;
    let d;
    try { d = await api(base()); } catch (e) {
      if (S.pid === pid) { S.err = "Can't read the cards: " + e.message; S.data = S.data || { cards: [], readonly: "" }; draw(); }
      return;
    }
    if (S.pid !== pid) return;
    S.data = d;
    if (S.err.startsWith("Can't read")) S.err = "";
    if (S.open && !ours() && !lent()) forget();   // the panel went to something else meanwhile
    draw();
  }

  async function openLib() {
    S.lib = { loading: true };
    draw();
    try { S.lib = await api("/api/cards/library"); } catch (e) { S.lib = { templates: [], pool: [], err: e.message }; }
    draw();
  }

  async function add(body) {
    try {
      const got = await api(base(), body);
      S.lib = null; S.err = "";
      await load();
      const name = got.card && (got.card.name || got.card);
      if (typeof name === "string") openCard(name);
    } catch (e) { if (S.lib) S.lib.err = e.message; else S.err = "Can't add the card: " + e.message; draw(); }
  }

  async function remove(name) {
    if (!confirm(`Remove the card "${name}"? Its folder components/${name}/ moves to the Trash.`)) return;
    try {
      await api(base() + "/" + encodeURIComponent(name), {}, "DELETE");
      S.err = "";
      if (S.open === name) closeCard();
    } catch (e) { S.err = `Can't remove ${name}: ${e.message}`; }
    load();
  }

  // ---- the open card in the side panel ---------------------------------------------------------

  const cardOf = (name) => ((S.data || {}).cards || []).find((x) => x.name === name);
  const subOf = (c) => (c.title || c.kind || "?") + (c.about ? " · " + c.about : "");
  const ours = () => !!(S.owner && window.panel && panel.owner() === S.owner);
  // the panel shows a file opened from our card, with "← back to" it
  function lent() {
    const p = document.getElementById("panel"), b = document.getElementById("panel-back");
    return !!(S.owner && p && b && !p.hidden && !b.hidden && b.textContent === "← back to " + S.owner.name);
  }
  function regrid() { S.last[".pc-grid"] = null; draw(); }
  function forget() { S.open = null; S.owner = null; regrid(); }

  // one owner per open card: panel.js draws it, swaps it for another, lends the panel to a file, closes it
  function owner(c) {
    const o = {
      name: c.name, sub: subOf(c),
      draw(body) {
        const now = cardOf(o.name) || c;
        if (window.projectCardPanel) projectCardPanel.open(body, S.pid, now, (S.data || {}).readonly || "",
          { api, ago, reload: load, close: () => panel.close() });
      },
      close(why) {
        window.projectCardPanel && projectCardPanel.close();
        if ((why === "show" || why === "close") && S.owner === o) forget();
      },
    };
    return o;
  }

  function openCard(name) {
    if (!window.panel) return;
    if (S.open === name && ours()) return panel.close();
    const c = cardOf(name);
    if (!c) return;
    if (S.open !== name || !S.owner) S.owner = owner(c);   // the same card, lent to a file: hosted again
    S.open = name;
    regrid();
    panel.host(S.owner);
  }

  // ours closes through the panel (its close passes "close" back); otherwise only the mark goes
  function closeCard() {
    if (ours()) return panel.close();
    if (S.open) forget();
  }

  function onClick(e) {
    const t = e.target;
    const F = filter();
    if (F && PF().click(F, t, (S.data || {}).cards || [])) return draw();
    if (F) put(".pc-filter", PF().html(F, (S.data || {}).cards || []));   // a list left open closes
    if (t.closest("a[href]")) return;                 // a serving link opens in its own tab
    const rm = t.closest("[data-rm]");
    if (rm) { e.stopPropagation(); return remove(rm.dataset.rm); }
    const lib = t.closest("[data-lib]");
    if (lib) { if (lib.dataset.lib === "open" && !S.lib) return openLib(); S.lib = null; return draw(); }
    const sg = t.closest("[data-suggest]");
    if (sg) { sg.disabled = true; return add({ template: sg.dataset.suggest }); }
    const tpl = t.closest("[data-tpl]");
    if (tpl) return add({ template: tpl.dataset.tpl });
    const pool = t.closest("[data-pool]");
    if (pool) return add({ pool: pool.dataset.pool });
    const card = t.closest("[data-card]");
    if (card) openCard(card.dataset.card);
  }

  function onKey(e) {
    const F = filter();
    if (F && PF().key(F, e, (S.data || {}).cards || [])) {
      draw();
      const b = q(".pc-fbtn");
      if (b && b.focus) b.focus();
      return;
    }
    if (e.key !== "Enter" || !e.target.matches || !e.target.matches(".pc-tile")) return;
    e.preventDefault();
    onClick(e);
  }

  function mount(el, pid) {
    if (!el) return;
    if (S.el !== el) {
      el.innerHTML = `<section class="pc-area"><h3 class="pc-h">Cards <span class="pj-sum dim pc-sum"></span>
        <span class="pc-filter"></span></h3>
        <div class="pc-note"></div><div class="pc-suggest"></div><div class="pc-grid"></div><div class="pc-libbox"></div></section>`;
      el.addEventListener("click", onClick);
      el.addEventListener("keydown", onKey);
    }
    if (S.pid !== pid) { closeCard(); S.open = null; S.owner = null; S.lib = null; S.err = ""; S.filter = null; }
    S.el = el; S.pid = pid; S.data = null; S.last = {};
    load();
    clearInterval(S.timer);
    S.timer = setInterval(load, 5000);
  }

  function unmount() {
    clearInterval(S.timer);
    S.timer = null;
    if (ours()) panel.close();
    S.pid = null; S.data = null; S.open = null; S.owner = null; S.lib = null; S.err = ""; S.filter = null;
    if (S.el) put(".pc-grid", "");
  }

  // the panels and the look come with this file (project.js only mounts it)
  // a press outside the open type list closes it
  if (document.addEventListener) document.addEventListener("pointerdown", (e) => {
    if (S.filter && S.filter.open && !(e.target.closest && e.target.closest(".pc-filter"))) {
      S.filter.open = false;
      draw();
    }
  });

  for (const src of ["/ui/projectcardfilter.js", "/ui/projectcardpanel.js", "/ui/projectinstructions.js"]) {
    if (!document.querySelector || document.querySelector(`script[src="${src}"]`)) continue;
    const t = document.createElement("script");
    t.src = src; t.async = false;
    document.head.appendChild(t);
  }
  if (document.querySelector && !document.querySelector('link[href="/ui/projectcards.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/projectcards.css";
    document.head.appendChild(l);
  }

  window.projectCards = { mount, unmount };
})();
