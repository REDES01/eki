// An open instructions card (projectcardpanel.js hands it over): the project's AGENTS.md rendered
// with md.js, Edit to turn it into a text box (saved when you leave it or on ⌘S, then rendered
// again), the files it points at under "Read first" (each opens read-only here, with a way back),
// and a "Link CLAUDE.md" fix when CLAUDE.md doesn't include the file. Routes: eki/api_cards.py.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const I = { el: null, pid: null, card: null, ro: "", ctx: null, mode: "view", read: null, err: "", saving: false, last: {} };

  const url = (tail) => "/api/projects/" + encodeURIComponent(I.pid) + "/cards/" + encodeURIComponent(I.card.name) + (tail || "");
  const q = (sel) => I.el && I.el.querySelector(sel);
  function put(sel, html) {
    const el = q(sel);
    if (el && I.last[sel] !== html) { el.innerHTML = html; I.last[sel] = html; }
  }
  const info = () => I.card.instructions || { file: "AGENTS.md", exists: false, heading: "", lines: 0, text: "", read_first: [], claude: "none" };
  const editable = () => !I.ro && !I.card.ref;
  // the project folder: a project card lives in <folder>/components/<name>
  const root = () => { const m = /^(.*)\/components\/[^/]+\/?$/.exec(String(I.card.card || "")); return m ? m[1] : ""; };
  const beside = (rel) => root() ? `<button type="button" class="pj-btn pi-beside" data-file="${esc(root() + "/" + rel)}"
      title="Open beside the chat">Open in panel</button>` : "";

  function head() {
    const c = I.card, f = info();
    const edit = editable() && I.mode === "view" ? '<button type="button" class="pj-btn" data-pi="edit">Edit</button>' : "";
    return `<div class="pj-row"><b class="pc-pname">${esc(c.name)}</b><span class="dim">instructions</span>
        <code class="pi-file">${esc(f.file)}</code>${f.exists ? `<span class="dim">${esc(f.lines)} lines</span>${beside(f.file)}` : ""}
        <span class="grow"></span>${edit}<button type="button" class="pj-btn" data-pi="close">Close</button></div>
      ${c.about ? `<div class="pj-sub">${esc(c.about)}</div>` : ""}
      ${c.error ? `<div class="pj-err">${esc(c.error)}</div>` : ""}
      ${c.ref ? `<div class="pj-sub">From the pool component <b>${esc(c.ref)}</b> — read-only here.</div>`
        : I.ro ? `<div class="pj-sub">Read-only: ${esc(I.ro)}.</div>` : ""}`;
  }

  function claude() {
    const f = info();
    if (f.claude !== "unlinked") return "";
    return `<div class="pj-row pi-claude-row"><span class="grow">CLAUDE.md doesn't include <code>@${esc(f.file)}</code>
        — Claude Code won't read these instructions.</span>
      ${I.ro ? "" : '<button type="button" class="pj-btn" data-pi="link">Link CLAUDE.md</button>'}</div>`;
  }

  function body() {
    const f = info();
    if (I.mode === "read" && I.read) {
      const r = I.read;
      return `<div class="pj-row"><a href="#" class="pi-back" data-pi="back">← Back to ${esc(f.file)}</a>
          <span class="grow"></span><code class="dim">${esc(r.path)}</code><span class="dim">read-only</span></div>
        ${r.err ? `<div class="pj-err">${esc(r.err)}</div>` : r.text == null ? '<div class="dim">Loading…</div>'
          : `<div class="pi-md">${md.render(r.text)}</div>`}`;
    }
    if (I.mode === "edit") {
      return `<div class="pp-label">${esc(f.file)} <span class="dim">saved when you leave the box or press ⌘S</span></div>
        <textarea class="set-in pi-text" rows="${Math.min(30, Math.max(10, (f.text || "").split("\n").length + 2))}" spellcheck="false">${esc(f.text || "")}</textarea>`;
    }
    if (!f.exists) return `<div class="dim">${esc(f.file)} is missing in the project folder.${editable() ? " Edit to write it." : ""}</div>`;
    return `<div class="pi-md">${md.render(f.text || "") || '<span class="dim">(empty)</span>'}</div>`;
  }

  function first() {
    const list = info().read_first || [];
    if (!list.length) return "";
    return `<h4 class="pp-h">Read first</h4>${list.map((p) =>
      `<div class="pj-item pp-line${I.mode === "read" && I.read && I.read.path === p ? " on" : ""}" data-read="${esc(p)}" role="button"><code class="grow">${esc(p)}</code>${beside(p)}</div>`).join("")}`;
  }

  function frame() {
    I.el.innerHTML = `<div class="pj-card pp pi"><div class="pi-head"></div><div class="pi-claude"></div>
      <div class="pi-body"></div><div class="pi-err"></div><div class="pi-first"></div></div>`;
    I.last = {};
  }

  function draw() {
    if (!I.el || !I.card) return;
    put(".pi-head", head());
    put(".pi-claude", claude());
    const a = document.activeElement;
    const typing = I.mode === "edit" && a && a.matches && a.matches(".pi-text") && I.el.contains(a);
    if (!typing && !I.saving) put(".pi-body", body());           // what you are typing stays
    put(".pi-err", I.err ? `<div class="pj-err">${esc(I.err)}</div>` : "");
    put(".pi-first", first());
  }

  function edit() {
    I.mode = "edit"; I.err = "";
    draw();
    const box = q(".pi-text");
    if (box) box.focus();
  }

  async function save(box) {
    if (I.saving || I.mode !== "edit" || !I.card) return;
    const text = box.value;
    if (text === (info().text || "")) { I.mode = "view"; return draw(); }
    I.saving = true;
    const name = I.card.name;
    try {
      const got = await I.ctx.api(url("/instructions"), { text });
      if (!I.card || I.card.name !== name) return;
      if (got.card && typeof got.card === "object") I.card = got.card;
      I.err = ""; I.mode = "view";
      I.ctx.reload();
    } catch (x) {
      I.err = `Can't save ${info().file}: ${x.message}`;   // the text box stays, with what you wrote
    } finally { I.saving = false; }
    draw();
  }

  async function read(path) {
    if (I.mode === "edit") return;
    I.mode = "read"; I.read = { path, text: null };
    draw();
    try {
      const got = await I.ctx.api(url("/read?path=" + encodeURIComponent(path)));
      if (I.read && I.read.path === path) I.read.text = got.text || "";
    } catch (x) { if (I.read && I.read.path === path) I.read.err = `Can't read ${path}: ${x.message}`; }
    draw();
  }

  async function link() {
    try {
      const got = await I.ctx.api(url("/link-claude"), {});
      if (got.card && typeof got.card === "object") I.card = got.card;
      I.err = "";
      I.ctx.reload();
    } catch (x) { I.err = "Can't link CLAUDE.md: " + x.message; }
    draw();
  }

  function onEvent(e) {
    if (!I.card) return;
    const t = e.target;
    if (e.type === "focusout") return t.matches && t.matches(".pi-text") && save(t);
    if (e.type === "keydown") {
      if ((e.metaKey || e.ctrlKey) && (e.key || "").toLowerCase() === "s" && t.matches && t.matches(".pi-text")) {
        e.preventDefault();
        save(t);
      }
      return;
    }
    if (e.type !== "click" || t.closest("[data-file]")) return;   // panel.js opens that one beside the chat
    const b = t.closest("[data-pi]");
    if (b) {
      e.preventDefault();
      const act = b.dataset.pi;
      if (act === "close") return I.ctx.close();
      if (act === "edit") return edit();
      if (act === "link") return link();
      if (act === "back") { I.mode = "view"; I.read = null; return draw(); }
    }
    const r = t.closest("[data-read]");
    if (r) return read(r.dataset.read);
  }

  function open(el, pid, card, ro, ctx) {
    close();
    if (!el) return;
    Object.assign(I, { el, pid, card, ro, ctx, mode: "view", read: null, err: "", saving: false });
    if (!el.dataset.piWired) {
      el.dataset.piWired = "1";
      for (const ev of ["click", "focusout", "keydown"]) el.addEventListener(ev, onEvent);
    }
    frame();
    draw();
  }

  // the cards reloaded: the card as it is now (the text box keeps what you type)
  function update(card, ro) {
    if (!I.card || !I.el || card.name !== I.card.name) return;
    I.card = card; I.ro = ro;
    if (I.mode === "edit" && !editable()) I.mode = "view";
    draw();
  }

  function close() {
    if (I.el && I.card) I.el.innerHTML = "";
    Object.assign(I, { card: null, mode: "view", read: null, err: "", last: {} });
  }

  window.projectInstructions = { open, update, close };
})();
