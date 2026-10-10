// Customize: #customize/skills and #customize/connectors. The frame is here — the head (search,
// sort, Add), the Skills · Connectors tabs and the Yours / Discover switch; the body belongs to
// window.customizeSkills or window.customizeConnectors, which each give
// {load(ctx), html(ctx), add(ctx), click(e, ctx)}. Everything comes from the API in
// eki/api_customize.py; the page keeps only the search, the sort (in localStorage) and what's open.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const TABS = ["skills", "connectors"];
  const SORTS = { name: "Name", recent: "Recently added" };
  const S = { on: false, tab: "skills", mode: "yours", q: "", sort: sortKept(), open: new Set(), data: {}, errs: {} };

  function sortKept() {
    try { const s = localStorage.getItem("eki.customize.sort"); return s in SORTS ? s : "name"; } catch (e) { return "name"; }
  }

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  // what a part sees: the state, the helpers, and a way to draw itself again
  const part = () => (S.tab === "connectors" ? window.customizeConnectors : window.customizeSkills);
  const ctx = () => ({ S, api, esc, sheet, folderField, filter, sorted, draw, reload: load });

  // the search: every word in one of the given fields, case aside
  function filter(list, keys) {
    const words = S.q.toLowerCase().split(/\s+/).filter(Boolean);
    return list.filter((x) => words.every((w) => keys.some((k) => String(x[k] || "").toLowerCase().includes(w))));
  }

  // the sort: by name, or newest first where an item says when it was added (the rest after, by name)
  function sorted(list) {
    const byName = (a, b) => String(a.name || a.title || "").localeCompare(String(b.name || b.title || ""));
    if (S.sort !== "recent") return [...list].sort(byName);
    return [...list].sort((a, b) => (b.added_at || 0) - (a.added_at || 0) || byName(a, b));
  }

  function box() {
    let el = $("customize-view");
    if (el) return el;
    el = document.createElement("div");
    el.id = "customize-view";
    el.innerHTML = `<div class="page">
      <div class="page-head"><h1 class="page-title">Customize</h1>
        <div class="actions">
          <div class="search" id="cz-search"><button type="button" data-cz="search" title="Search">⌕</button>
            <input id="cz-q" placeholder="Search" spellcheck="false"></div>
          <select id="cz-sort" class="btn" title="Sort">${Object.entries(SORTS).map(([k, v]) =>
            `<option value="${k}"${k === S.sort ? " selected" : ""}>${v}</option>`).join("")}</select>
          <button type="button" class="btn-primary" data-cz="add">Add</button></div></div>
      <nav class="tabs" id="cz-tabs">${TABS.map((t) => `<a href="#customize/${t}" data-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</a>`).join("")}</nav>
      <div class="seg" id="cz-seg"><button type="button" data-mode="yours">Yours</button><button type="button" data-mode="discover">Discover</button></div>
      <div id="cz-body"></div></div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", click);
    $("cz-q").addEventListener("input", () => { S.q = $("cz-q").value; draw(); });
    $("cz-sort").addEventListener("change", () => {
      S.sort = $("cz-sort").value in SORTS ? $("cz-sort").value : "name";
      try { localStorage.setItem("eki.customize.sort", S.sort); } catch (e) { /* fine */ }
      draw();
    });
    style();
    return el;
  }

  // the few looks only this page has; everything else is pages.css
  function style() {
    if ($("cz-style")) return;
    const s = document.createElement("style");
    s.id = "cz-style";
    s.textContent = `#customize-view .seg { margin-bottom: 16px; }
      .cz-rows { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 12px; }
      .cz-row { padding: 12px 16px; border-top: 1px solid var(--line); }
      .cz-row:first-child { border-top: 0; }
      .cz-line { display: flex; align-items: center; gap: 10px; cursor: pointer; min-width: 0; }
      .cz-line .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .cz-more { margin-top: 8px; font-size: 13px; }
      .cz-more code { word-break: break-all; }
      .cz-switch { width: 34px; height: 20px; border-radius: 10px; border: 1px solid var(--line);
        background: var(--panel); position: relative; cursor: pointer; flex: none; padding: 0; }
      .cz-switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
        border-radius: 50%; background: var(--dim); transition: left .12s; }
      .cz-switch.on { background: var(--text); }
      .cz-switch.on::after { left: 16px; background: var(--bg); }
      .cz-card-foot { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
      .cz-card-foot .quiet { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .cz-err { color: var(--red); font-size: 13px; }`;
    document.head.appendChild(s);
  }

  function draw() {
    const el = $("cz-body"), p = part();
    if (!el) return;
    el.innerHTML = p ? p.html(ctx()) : '<div class="quiet">Not available in this build.</div>';
  }

  async function load() {
    const p = part(), tab = S.tab, mode = S.mode;
    if (!p) return draw();
    try { await p.load(ctx()); delete S.errs.load; } catch (x) { S.errs.load = x.message; }
    if (S.on && S.tab === tab && S.mode === mode) draw();
  }

  // the tabs and the switch show where you are; Add means the current tab's own sheet
  function marks() {
    document.querySelectorAll("#cz-tabs [data-tab]").forEach((a) => a.classList.toggle("on", a.dataset.tab === S.tab));
    document.querySelectorAll("#cz-seg [data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
  }

  function mode(m) {
    S.mode = m === "discover" ? "discover" : "yours";
    S.open.clear();
    marks();
    draw();
    load();
  }

  async function click(e) {
    const m = e.target.closest("[data-mode]");
    if (m) return mode(m.dataset.mode);
    const c = e.target.closest("[data-cz]");
    if (c && c.dataset.cz === "search") {
      const s = $("cz-search");
      s.classList.toggle("open");
      if (s.classList.contains("open")) $("cz-q").focus();
      return;
    }
    if (c && c.dataset.cz === "add") { const p = part(); return p && p.add(ctx()); }
    const p = part();
    if (p) return p.click(e, ctx());
  }

  // A modal with the card look. fn(dialog) does the work; what it throws shows inline and the
  // sheet stays open; when it's done the sheet closes and the page loads again.
  function sheet(title, inner, label, fn) {
    const d = document.createElement("dialog");
    d.className = "sheet";
    d.innerHTML = `<form autocomplete="off"><h2>${esc(title)}</h2>${inner}
      <div class="error" hidden></div>
      <div class="row-end"><button type="button" class="btn" data-x="cancel">Cancel</button>
        <button type="submit" class="btn-primary">${esc(label)}</button></div></form>`;
    document.body.appendChild(d);
    const err = d.querySelector(".error");
    d.addEventListener("close", () => d.remove());
    d.querySelector('[data-x="cancel"]').addEventListener("click", () => d.close());
    d.querySelector("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      try { await fn(d); d.close(); load(); } catch (x) { err.textContent = x.message; err.hidden = false; }
    });
    d.showModal();
    return d;
  }

  // A folder path field with completion from /api/folders and, in the Mac shell, "Choose…".
  // Returns its html; wire(dialog) hooks it up once the sheet is open.
  function folderField(name, placeholder) {
    const list = `cz-dl-${name}`;
    const html = `<div class="cz-line"><input name="${name}" list="${list}" class="grow" placeholder="${esc(placeholder || "~/folder")}" spellcheck="false">
      ${window.ekiShell && ekiShell.canPickFolder() ? `<button type="button" class="btn" data-x="choose">Choose…</button>` : ""}</div>
      <datalist id="${list}"></datalist>`;
    function wire(d) {
      const input = d.querySelector(`[name="${name}"]`), dl = d.querySelector(`#${list}`);
      let n = 0;
      input.addEventListener("input", async () => {
        const mine = ++n;
        try {
          const got = await api("/api/folders?prefix=" + encodeURIComponent(input.value));
          if (mine === n) dl.innerHTML = (got.folders || []).map((f) => `<option value="${esc(f)}">`).join("");
        } catch (x) { /* no completion, typing still works */ }
      });
      const choose = d.querySelector('[data-x="choose"]');
      if (choose) choose.addEventListener("click", async () => { const p = await ekiShell.pickFolder(); if (p) input.value = p; });
    }
    return { html, wire };
  }

  function show(tab) {
    const el = box();
    el.hidden = false;
    const t = TABS.includes(tab) ? tab : "skills";
    if (t !== S.tab) { S.open.clear(); S.mode = "yours"; }
    S.on = true; S.tab = t;
    $("title").textContent = "Customize";
    $("meta").textContent = "";
    marks();
    draw();
    load();
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    box().hidden = true;
  }

  window.customize = { show, hide, state: S, ctx, mode, click, filter, sorted, api, load };
  nav.register("customize", { show, hide });
})();
