// The menu on a chat in the sidebar: right-click a .thread, or click its … (.tmore). Copy the ids, pin, mark unread,
// rename in place, move to a project or one of your groups, archive, delete. Every change is POST /api/threads/<id>,
// then side.loadThreads().
(function () {
  let menu = null, cur = null;   // the open menu and the entry it is for

  async function call(path, body, method) {
    const opts = { method: method || (body === undefined ? "GET" : "POST"), headers: { "X-Eki": "1" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  function say(msg) { const e = document.getElementById("engine"); if (e) e.textContent = msg; }
  function reload() { if (window.side) window.side.loadThreads().catch(() => {}); }

  async function act(id, body) {
    try { await call(`/api/threads/${id}`, body); } catch (e) { say(e.message); }
    reload();
  }

  // the entry side.js last drew; a bare one when it isn't there (the list moved on)
  function find(id) {
    const list = window.side && window.side.list ? window.side.list() : [];
    return list.find((t) => t.id === id) || { id, sessions: [] };
  }

  // the Mac shell's web view may refuse the clipboard API: a hidden textarea and execCommand("copy") then
  async function copy(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) return await navigator.clipboard.writeText(text);
    } catch (e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.opacity = "0"; ta.style.left = "-9999px";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } finally { ta.remove(); }
  }

  // ---- rename: the .t span edits in place; the 4-second redraw puts the edit back where it was --------------------

  let editing = null;   // {id, text, old}
  function span(id) { return document.querySelector(`#threads .thread[data-id="${id}"] .t`); }

  function startEdit(id, old, text) {
    const el = span(id);
    if (!el) return;
    editing = { id, old, text: text === undefined ? old : text, el };
    el.textContent = editing.text;
    el.contentEditable = "true"; el.classList.add("editing"); el.spellcheck = false;
    el.focus();
    const r = document.createRange(); r.selectNodeContents(el);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    el.addEventListener("input", () => { if (editing && editing.el === el) editing.text = el.textContent; });
    el.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finishEdit(true); }
      else if (e.key === "Escape") { e.preventDefault(); finishEdit(false); }
      e.stopPropagation();
    });
    el.addEventListener("blur", () => setTimeout(() => {
      if (editing && editing.el === el && el.isConnected) finishEdit(false);   // a redraw removed it: kept, not cancelled
    }, 0));
  }

  function finishEdit(save) {
    if (!editing) return;
    const { id, old, el } = editing;
    const title = el.textContent.trim();
    editing = null;
    el.contentEditable = "false"; el.classList.remove("editing");
    if (!save || !title || title === old) { el.textContent = old; return; }
    el.textContent = title;
    act(id, { title });
  }

  function watchRedraw() {
    const box = document.getElementById("threads");
    if (!box || typeof MutationObserver === "undefined") return;
    new MutationObserver(() => {
      if (editing && !editing.el.isConnected) startEdit(editing.id, editing.old, editing.text);
    }).observe(box, { childList: true, subtree: true });
  }

  // ---- the menu -----------------------------------------------------------------------------------------------------

  function esc(s) { return window.md ? md.esc(s) : String(s); }

  function item(label, key, a, extra) {
    const x = extra || {};
    return `<button type="button" class="tmitem ${x.cls || ""}" data-act="${a}"${x.arg !== undefined ? ` data-arg="${esc(x.arg)}"` : ""}
      ${x.off ? `disabled title="${esc(x.off)}"` : ""}><span>${esc(label)}</span>${key ? `<kbd>${key}</kbd>` : ""}</button>`;
  }

  function items(t) {
    let html = item("Copy session ID", "C", "copy");
    (t.sessions || []).forEach((s, i) => { html += item(`Copy ${s.label} session ID`, i ? "" : "S", "session", { arg: i }); });
    html += '<div class="tmsep"></div>';
    html += item(t.pinned ? "Unpin" : "Pin", "P", "pin");
    html += item("Mark as unread", "U", "unread");
    html += item("Rename", "R", "rename");
    html += '<div class="tmsep"></div>';
    html += `<div class="tmsubwrap" data-sub="project">${item("Add to project", "›", "sub", { cls: "tmhas" })}</div>`;
    html += `<div class="tmsubwrap" data-sub="group">${item("Move to group", "›", "sub", { cls: "tmhas" })}</div>`;
    html += '<div class="tmsep"></div>';
    html += item("Archive", "A", "archive");
    html += item("Delete", "D", "delete", { cls: "red", off: t.own ? "eki's own thread: drop its work from the Station" : "" });
    return html;
  }

  function open(id, x, y) {
    close();
    cur = find(id);
    menu = document.createElement("div");
    menu.className = "tmenu"; menu.setAttribute("role", "menu");
    menu.innerHTML = items(cur);
    document.body.appendChild(menu);
    const w = menu.offsetWidth || 220, h = menu.offsetHeight || 300;
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - w - 4)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - h - 4)) + "px";
  }

  function close() {
    if (menu) menu.remove();
    menu = null;
  }

  async function sub(wrap) {
    if (!menu) return;
    menu.querySelectorAll(".tmsub").forEach((s) => s.remove());
    const box = document.createElement("div");
    box.className = "tmsub";
    box.innerHTML = '<div class="tmnote dim">Loading…</div>';
    wrap.appendChild(box);
    const t = cur;
    try {
      if (wrap.dataset.sub === "project") {
        const got = await call("/api/projects");
        box.innerHTML = (got.projects || []).map((p) => item(p.name || p.id, p.id === (t.project && t.project.id) ? "✓" : "",
          "project", { arg: p.id, off: p.folder ? "" : "no folder yet: give it one on its page" })).join("") +
          '<div class="tmsep"></div>' + item("No project", "", "project", { arg: "" }) + item("New project…", "", "newproject");
        box.dataset.home = got.user_home || "";
      } else {
        const groups = await call("/api/groups");
        box.innerHTML = groups.map((g) => item(g.name, g.id === (t.group && t.group.id) ? "✓" : "", "group", { arg: g.id })).join("") +
          '<div class="tmsep"></div>' + item("New group…", "", "newgroup") + item("No group", "", "group", { arg: "" });
      }
    } catch (e) { box.innerHTML = `<div class="tmnote dim">${esc(e.message)}</div>`; }
  }

  async function run(a, arg, home) {
    const t = cur, id = t.id;
    close();
    if (a === "copy") return copy(id);
    if (a === "session") { const s = (t.sessions || [])[+arg || 0]; return s && copy(s.id); }
    if (a === "pin") return act(id, { pin: !t.pinned });
    if (a === "unread") return act(id, { unread: true });
    if (a === "rename") return startEdit(id, t.title || (span(id) || {}).textContent || "");
    if (a === "archive") return act(id, { archive: true });
    if (a === "project") return act(id, { project: arg || null });
    if (a === "group") return act(id, { group: arg || null });
    if (a === "newproject") {
      const name = (prompt("Name of the new project") || "").trim();
      if (!name) return;
      const folder = prompt("Its folder", home ? `${home}/${name}` : "");
      if (folder === null) return;
      try {
        const p = await call("/api/projects", { name, folder: folder.trim() || null });
        return act(id, { project: p.id });
      } catch (e) { say(e.message); return reload(); }
    }
    if (a === "newgroup") {
      const name = (prompt("Name of the new group") || "").trim();
      if (!name) return;
      try {
        const g = await call("/api/groups", { name });
        return act(id, { group: g.id });
      } catch (e) { say(e.message); return reload(); }
    }
    if (a === "delete") {
      if (t.own || !confirm("Delete this conversation? Files it changed stay.")) return;
      try {
        await call(`/api/threads/${id}`, undefined, "DELETE");
        if (window.nav && nav.current && nav.current() === id) location.hash = "";
      } catch (e) { say(e.message); }
      return reload();
    }
  }

  const KEYS = { c: "copy", s: "session", p: "pin", u: "unread", r: "rename", a: "archive", d: "delete" };

  document.addEventListener("contextmenu", (e) => {
    const th = e.target.closest && e.target.closest(".thread[data-id]");
    if (!th || (editing && editing.id === th.dataset.id)) return;
    e.preventDefault();
    open(th.dataset.id, e.clientX, e.clientY);
  });

  document.addEventListener("click", (e) => {
    const more = e.target.closest && e.target.closest(".tmore");
    if (more) {
      e.preventDefault();
      const r = more.getBoundingClientRect();
      return open(more.dataset.tmore || more.closest(".thread").dataset.id, r.left, r.bottom + 2);
    }
    if (!menu || !menu.contains(e.target)) return;
    const b = e.target.closest(".tmitem");
    if (!b || b.disabled) return;
    if (b.dataset.act === "sub") return sub(b.closest(".tmsubwrap"));
    const box = b.closest(".tmsub");
    run(b.dataset.act, b.dataset.arg, box ? box.dataset.home : "");
  });

  document.addEventListener("mouseover", (e) => {
    const wrap = menu && e.target.closest && e.target.closest(".tmsubwrap");
    if (wrap && !wrap.querySelector(".tmsub")) sub(wrap);
  });

  document.addEventListener("mousedown", (e) => { if (menu && !menu.contains(e.target)) close(); });

  document.addEventListener("keydown", (e) => {
    if (!menu) return;
    if (e.key === "Escape") { e.preventDefault(); return close(); }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const a = KEYS[(e.key || "").toLowerCase()];
    if (!a) return;
    if (a === "session" && !(cur.sessions || []).length) return;
    if (a === "delete" && cur.own) return;
    e.preventDefault();
    run(a, a === "session" ? 0 : undefined, "");
  });

  window.addEventListener("blur", close);
  window.addEventListener("resize", close);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchRedraw);
  else watchRedraw();

  window.threadmenu = { open, close, copy };
})();
