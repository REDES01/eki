// The knowledge card's own part in an open card (projectcardpanel.js draws it under "Documents", on open
// and on every 5 s update): each document of the card (cardknowledgedocs.js draws its head — name, read
// dropdown, about, size, last change, × — and "+ document"), then each file it names — name, size, last
// changed, who changed it last, or "missing". The card's docs, files and read are drawn here, not in the
// settings form. In a project card an existing file's row is a [data-file] row: panel.js opens it in the
// side panel's doc viewer (with its Edit) and a "← back to <card>" link. A missing file opens here, with
// Edit turning it into a text box saved when you leave it or press ⌘S/Ctrl-S (a save error shows beside
// it) — the only way to write one. A pool card (no project folder) shows its files here read-only, rendered
// with md.js, since the doc viewer can't open ~/.eki. The 5 s reload redraws the list only, never the box
// you're typing in. Read-only when the project has no folder or the card is a pool reference. Routes: eki/api_cardknowledge.py (GET …/cards/<name>/files, GET/POST …/cards/<name>/file).
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  if (document.querySelector && !document.querySelector('link[href="/ui/cardknowledge.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardknowledge.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  if (document.querySelector && document.createElement && !document.querySelector('script[src="/ui/cardknowledgedocs.js"]')) {
    const t = document.createElement("script");
    t.src = "/ui/cardknowledgedocs.js"; t.onload = () => { if (K.el) redraw(); };
    document.head.appendChild(t);
  }
  const K = { el: null, pid: null, card: null, ro: "", ctx: null, docs: null, files: null, err: "", loading: false,
              again: false, open: null, mode: "view", file: null, saveErr: "", last: {} };

  const url = (tail) => "/api/projects/" + encodeURIComponent(K.pid) + "/cards/" + encodeURIComponent(K.card.name) + tail;
  const q = (sel) => K.el && K.el.querySelector(sel);
  function put(sel, html) {
    const el = q(sel);
    if (el && K.last[sel] !== html) { el.innerHTML = html; K.last[sel] = html; }
  }
  const editable = () => !K.ro && !K.card.ref && !(K.ctx && K.ctx.editable === false);
  // the project folder: a project card lives in <folder>/components/<name> (the pool's ~/.eki/components isn't one)
  const root = () => { const m = /^(.*)\/components\/[^/]+\/?$/.exec(String(K.card.card || "")); return m && !/\/\.eki$/.test(m[1]) ? m[1] : ""; };
  // an existing file of a project card opens in the side panel; the rest open here
  const panelFile = (f) => !f.missing && root() && !/[*?[]/.test(f.path) ? root() + "/" + f.path : "";

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  function ago(t) {
    if (t == null || t === "") return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - Number(t)));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
    return new Date(Number(t) * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  }
  function size(n) {
    if (n == null) return "";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${Math.round(n / 102.4) / 10} KB`;
    return `${Math.round(n / 104857.6) / 10} MB`;
  }

  // ---- the list -------------------------------------------------------------------------------

  const D = () => window.cardKnowledgeDocs;
  // what cardknowledgedocs.js needs, as it is now
  const helper = () => ({ esc, size, ago, card: K.card, docs: K.docs, editable: !!K.card && editable(),
    problems: (K.ctx && K.ctx.problems) || {}, save: (set) => K.ctx ? K.ctx.save(set) : Promise.resolve() });

  function list() {
    if (K.err) return `<div class="pj-err">${esc(K.err)}</div>`;
    if (!K.docs) return '<div class="dim">Loading…</div>';
    const h = helper();
    if (!K.docs.length) return (D() ? D().top(h) : "") + '<div class="dim">No documents.</div>';
    return (D() ? D().top(h) : "") + K.docs.map((d, i) => `<div class="ck-doc">${D() ? D().head(d, i, h)
      : `<div class="ck-doc-head"><strong class="ck-doc-name">${esc(d.name)}</strong> <span class="dim">${esc(d.read || "")}</span></div>`}
      ${(d.files || []).length ? rows(d.files) : '<div class="dim">No files.</div>'}</div>`).join("");
  }

  function rows(files) {
    return files.map((f) => `<div class="pj-item pp-line ck-row${K.open === f.path ? " on" : ""}${f.missing ? " ck-missing" : ""}"
        ${panelFile(f) ? `data-file="${esc(panelFile(f))}"` : `data-ck-path="${esc(f.path)}"`} role="button" title="${esc(f.path)}">
        <code class="grow ck-name">${esc(f.path)}</code>
        ${f.missing ? '<span class="pj-err">missing</span>'
          : `<span class="dim ck-size">${esc(size(f.size))}</span>
             <span class="dim ck-when">${esc(ago(f.changed))}</span>
             <span class="dim ck-who">${esc(f.author || "")}</span>`}</div>`).join("");
  }

  async function load() {
    if (K.loading) { K.again = true; return; }
    K.loading = true; K.again = false;
    const name = K.card.name;
    try {
      const got = await api(url("/files"));
      if (!K.card || K.card.name !== name) return;
      // a server without docs answers files only: one document named after the card
      K.docs = got.docs || [{ name, about: K.card.about || "", read: (K.card.spec || {}).read || "", files: got.files || [] }];
      K.files = K.docs.flatMap((d) => d.files || []); K.err = "";
    } catch (x) { if (K.card && K.card.name === name) K.err = x.message; }
    finally { K.loading = false; }
    if (K.again && K.card) return load();      // a save landed while this one was loading
    const now = (K.files || []).find((e) => e.path === K.open);
    if (now && panelFile(now) && K.mode !== "edit") { K.open = null; K.file = null; put(".ck-file", ""); }  // written: the panel has it now
    redraw();
  }

  // ---- one file -------------------------------------------------------------------------------

  function filePart() {
    if (!K.open) return "";
    const f = K.file, entry = (K.files || []).find((e) => e.path === K.open) || {};
    const edit = editable() && K.mode === "view" && f && !f.err ? '<button type="button" class="pj-btn" data-ck="edit">Edit</button>' : "";
    const head = `<div class="pj-row"><code class="grow">${esc(K.open)}</code>${edit}
        <button type="button" class="pj-btn" data-ck="close">Close</button></div>`;
    if (!f) return head + '<div class="dim">Loading…</div>';
    if (f.err) return head + `<div class="pj-err">${esc(f.err)}</div>`;
    if (K.mode === "edit") {
      const rows = Math.min(30, Math.max(10, f.text.split("\n").length + 2));
      return head + `<div class="pp-label">saved when you leave the box or press ⌘S <span class="ck-err"></span></div>
        <textarea class="set-in ck-text" rows="${rows}" spellcheck="false">${esc(f.text)}</textarea>`;
    }
    const body = entry.missing && !f.text ? `<span class="dim">missing in the project folder.${editable() ? " Edit to write it." : ""}</span>`
      : md.render(f.text) || '<span class="dim">(empty)</span>';
    return head + `<div class="ck-md">${body}</div>${K.saveErr ? `<div class="pj-err">${esc(K.saveErr)}</div>` : ""}`;
  }

  async function openFile(path) {
    if (K.open === path && K.mode === "view") { K.open = null; K.file = null; return put(".ck-file", filePart()); }
    Object.assign(K, { open: path, file: null, mode: "view", saveErr: "" });
    put(".ck-list", list()); put(".ck-file", filePart());
    const entry = (K.files || []).find((e) => e.path === path) || {};
    try {
      K.file = entry.missing ? { path, text: "" } : await api(url("/file?path=" + encodeURIComponent(path)));
    } catch (x) { K.file = { err: x.message }; }
    if (K.open === path) put(".ck-file", filePart());
  }

  async function save(box, leave) {
    if (!K.file || K.mode !== "edit") return;
    const path = K.open, text = box.value;
    const shown = (msg) => { const e = q(".ck-err"); if (e) e.innerHTML = msg ? `<span class="pj-err">${esc(msg)}</span>` : ""; };
    if (text !== K.file.text) {
      try {
        await api(url("/file"), { path, text });
        if (K.open !== path) return;
        K.file.text = text; K.saveErr = "";
        shown("");
      } catch (x) {
        if (K.open !== path) return;
        K.saveErr = x.message;
        shown(x.message);
        return;                                 // the box stays so nothing you typed is lost
      }
      load();
    }
    if (leave) { K.mode = "view"; K.last[".ck-file"] = null; put(".ck-file", filePart()); }
  }

  // ---- the part -------------------------------------------------------------------------------

  function onEvent(e) {
    const t = e.target;
    if (e.type === "focusout") return t.matches && t.matches(".ck-text") && save(t, true);
    if (e.type === "keydown") {
      if ((e.metaKey || e.ctrlKey) && (e.key || "").toLowerCase() === "s" && t.matches && t.matches(".ck-text")) {
        e.preventDefault();
        save(t, false);
      }
      return;
    }
    if (e.type !== "click" || t.closest("[data-file]")) return;   // panel.js opens that one in the side panel
    const d = t.closest("[data-ckd]");
    if (d && D()) return D().act(d, K.el, helper(), redraw);
    const b = t.closest("[data-ck]");
    if (b && b.dataset.ck === "close") { K.open = null; K.file = null; K.mode = "view"; put(".ck-list", list()); return put(".ck-file", filePart()); }
    if (b && b.dataset.ck === "edit" && editable()) {
      K.mode = "edit"; put(".ck-file", filePart());
      const box = q(".ck-text");
      if (box) box.focus();
      return;
    }
    const row = t.closest("[data-ck-path]");
    if (row && K.mode !== "edit") return openFile(row.dataset.ckPath);
  }

  const FRAME = '<div class="ck"><div class="ck-list"></div><div class="ck-add"></div><div class="ck-file"></div></div>';

  // the list and the + document part, from what is known now
  function redraw() {
    if (!K.el || !K.card) return;
    if (D()) D().wire(K.el, helper, redraw);
    put(".ck-list", list());
    put(".ck-add", D() ? D().adder(helper()) : "");
  }

  function draw(el, card, pid, ro, ctx) {
    if (K.el !== el || !K.card || K.card.name !== card.name || K.pid !== pid) {
      close();
      Object.assign(K, { el, pid, card, ro });
      el.innerHTML = FRAME;
      if (!el.dataset.ckWired) {
        el.dataset.ckWired = "1";
        for (const ev of ["click", "focusout", "keydown"]) el.addEventListener(ev, onEvent);
      }
    }
    K.card = card; K.ro = ro; K.ctx = ctx || null;
    if (!q(".ck")) {                            // the panel framed itself again
      el.innerHTML = FRAME;
      K.last = {};
    }
    if (!editable() && K.mode === "edit") K.mode = "view";
    redraw();
    if (K.mode !== "edit") put(".ck-file", filePart());
    load();
  }

  function close() {
    Object.assign(K, { el: null, card: null, ctx: null, docs: null, files: null, err: "", open: null, mode: "view",
                       file: null, saveErr: "", last: {} });
    if (D()) D().reset();
  }

  window.projectCardKinds["knowledge"] = { title: "Documents", keys: ["docs", "files", "read"], draw, close };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
