// Open file… (⌘O): a path, or a fuzzy search over the project's markdown,
// opens in the side panel. With nothing typed it lists the recent files
// (kept per viewer in localStorage; storage may be blocked, so every touch
// is in try/catch). `eki open <path>` lands here too: the page polls the
// engine's open queue and shows the newest one.
(function () {
  const $ = (id) => document.getElementById(id);
  const KEY = "eki.recentDocs", MAX = 12, POLL = 2000, WAIT = 150;
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const S = { box: null, input: null, list: null, err: null, items: [], sel: 0, timer: 0, seq: 0, last: null };

  // ── recents ──
  function recent() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(v) ? v.filter((p) => typeof p === "string") : [];
    } catch (e) { return []; }
  }

  function remember(path) {
    if (!path) return;
    const list = [path, ...recent().filter((p) => p !== path)].slice(0, MAX);
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* storage blocked: no recents */ }
  }

  window.addEventListener("eki:doc", (e) => remember(e.detail && e.detail.path));

  // ── the current project: #project/<id>, or the project of the chat that's open ──
  async function project() {
    const name = window.nav && nav.current ? nav.current() : decodeURIComponent((location.hash || "").slice(1));
    const m = /^project\/(.+)$/.exec(name);
    if (m) return m[1];
    const view = window.nav && nav.route ? nav.route(name)[0] : "chat";
    if (!name || view !== "chat") return null;   // a view, not a thread: search everything
    try {
      const r = await fetch("/api/threads/" + encodeURIComponent(name));
      if (!r.ok) return null;
      const t = await r.json();
      const p = t && t.project;
      return p ? String(typeof p === "object" ? p.id : p) : null;
    } catch (e) { return null; }
  }

  // ── the dialog ──
  function build() {
    const css = document.createElement("style");
    css.id = "docopen-css";
    css.textContent = `
      .docopen-back { position: fixed; inset: 0; background: rgba(0,0,0,.25); z-index: 60; display: flex; justify-content: center; align-items: flex-start; }
      .docopen-back[hidden] { display: none; }
      .docopen { margin-top: 12vh; width: min(560px, 92vw); background: var(--bg, #fff); color: inherit; border: 1px solid var(--line, #ddd);
        border-radius: 10px; box-shadow: 0 12px 40px rgba(0,0,0,.2); overflow: hidden; }
      .docopen input { width: 100%; box-sizing: border-box; border: 0; border-bottom: 1px solid var(--line, #ddd); padding: 12px 14px;
        font: inherit; font-size: 15px; background: transparent; color: inherit; outline: none; }
      .docopen ul { list-style: none; margin: 0; padding: 4px 0; max-height: 50vh; overflow: auto; }
      .docopen li { padding: 6px 14px; cursor: pointer; display: flex; gap: 8px; align-items: baseline; }
      .docopen li.on { background: var(--hover, rgba(0,0,0,.06)); }
      .docopen li .dim, .docopen .hint { color: var(--dim, #888); font-size: 12px; }
      .docopen li .dim { margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .docopen .hint { padding: 8px 14px; }
      .docopen .docopen-err { color: var(--red, #c33); padding: 8px 14px; font-size: 13px; }
      .docopen .docopen-err[hidden] { display: none; }`;
    document.head.appendChild(css);
    const back = document.createElement("div");
    back.className = "docopen-back";
    back.hidden = true;
    const box = document.createElement("div");
    box.className = "docopen";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "A path (/… or ~/…) or search markdown files";
    input.setAttribute("spellcheck", "false");
    const err = document.createElement("div");
    err.className = "docopen-err";
    err.hidden = true;
    const list = document.createElement("ul");
    box.appendChild(input); box.appendChild(err); box.appendChild(list);
    back.appendChild(box);
    document.body.appendChild(back);
    Object.assign(S, { box: back, input, list, err });

    input.addEventListener("input", () => changed());
    input.addEventListener("keydown", key);
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    list.addEventListener("click", (e) => {
      const li = e.target.closest && e.target.closest("[data-i]");
      if (li) open(S.items[Number(li.dataset.i)].path);
    });
  }

  function isPath(text) { return /^[\/~]/.test(text); }

  function key(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); return close(); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!S.items.length) return;
      S.sel = (S.sel + (e.key === "ArrowDown" ? 1 : -1) + S.items.length) % S.items.length;
      return draw();
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const text = S.input.value.trim();
      if (isPath(text)) return open(text);
      const it = S.items[S.sel];
      if (it) open(it.path);
    }
  }

  function error(msg) {
    S.err.textContent = msg || "";
    S.err.hidden = !msg;
  }

  function draw(hint) {
    if (!S.items.length) { S.list.innerHTML = hint ? `<li class="hint">${esc(hint)}</li>` : ""; return; }
    S.list.innerHTML = S.items.map((it, i) => `<li data-i="${i}" class="${i === S.sel ? "on" : ""}" title="${esc(it.path)}">
      <span>${esc(it.label)}</span><span class="dim">${esc(it.where || "")}</span></li>`).join("");
  }

  function showRecent() {
    S.items = recent().map((p) => {
      const parts = p.split("/");
      return { path: p, label: parts.pop(), where: parts.join("/") };
    });
    S.sel = 0;
    draw("No recent files yet. Type a path or a few letters of a file name.");
  }

  function changed() {
    clearTimeout(S.timer);
    error("");
    const text = S.input.value.trim();
    if (!text) return showRecent();
    if (isPath(text)) { S.items = []; return draw("Enter opens " + text); }
    S.timer = setTimeout(() => search(text), WAIT);
  }

  async function search(text) {
    const seq = ++S.seq;
    const pid = await project();
    let url = "/api/doc/search?q=" + encodeURIComponent(text);
    if (pid) url += "&project=" + encodeURIComponent(pid);
    let data;
    try {
      const r = await fetch(url);
      data = await r.json().catch(() => ({}));
      if (!r.ok && !data.error) data.error = r.statusText || "Search failed.";
    } catch (e) { data = { error: "Can't reach eki right now." }; }
    if (seq !== S.seq) return;   // a newer search is under way
    if (data.error) { S.items = []; draw(); return error(data.error); }
    S.items = (data.files || []).map((f) => ({
      path: f.path, label: f.rel || f.path,
      where: pid ? "" : String(f.root || "").replace(/\/+$/, "").split("/").pop(),
    }));
    S.sel = 0;
    draw("No markdown files match.");
  }

  async function open(path) {
    if (!path) return;
    try {
      await panel.show(path);
      close();
    } catch (e) { error(String((e && e.message) || e || "Can't open this file.")); }
  }

  function show() {
    if (!S.box) build();
    S.box.hidden = false;
    S.input.value = "";
    error("");
    showRecent();
    S.input.focus();
  }

  function close() {
    if (!S.box || S.box.hidden) return;
    clearTimeout(S.timer);
    S.seq++;
    S.box.hidden = true;
  }

  // ── the sidebar link and ⌘O ──
  function link() {
    const n = $("new");
    if (!n || $("docopen-link")) return !!n;
    const a = document.createElement("a");
    a.id = "docopen-link";
    a.href = "#";
    a.className = "side-link";
    a.title = "Open file (⌘O)";
    a.textContent = "Open file…";
    a.addEventListener("click", (e) => { e.preventDefault(); show(); });
    n.parentNode.insertBefore(a, n.nextSibling);
    return true;
  }
  if (!link()) document.addEventListener("DOMContentLoaded", link);

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && String(e.key).toLowerCase() === "o") {
      e.preventDefault();
      show();
    }
  });

  // ── eki open: the engine's open queue ──
  async function poll() {
    const first = S.last === null;
    try {
      const r = await fetch("/api/doc/opens" + (first ? "" : "?after=" + encodeURIComponent(S.last)));
      if (!r.ok) return;
      const data = await r.json();
      if (typeof data.last === "number") S.last = Math.max(S.last || 0, data.last);
      const opens = data.opens || [];
      if (first || !opens.length) return;
      const newest = opens.reduce((a, b) => (Number(b.id) > Number(a.id) ? b : a));
      await panel.show(newest.path);
      try { window.focus(); } catch (e) { /* not allowed: fine */ }
    } catch (e) { /* the engine is restarting: wait for the next poll */ }
  }
  poll();
  setInterval(poll, POLL);

  window.docopen = { show, recent, close, poll };
})();
