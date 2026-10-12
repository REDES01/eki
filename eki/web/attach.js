// Pictures in the composer: paste, drop, or 📎. Each is uploaded at once
// (POST /api/attachments) and shown in a strip above the textarea; app.js
// sends ekiAttach.paths() with the ask and then calls ekiAttach.clear().
// Only the unsent strip lives here, so a reload loses nothing the engine has.
//
// ekiDrafts: the unsent text and pictures of each conversation, in this window's localStorage only (never sent to
// the engine, never synced): the person's own convenience. Keys: a thread's id, `new` for a new chat, `new:<project>`
// for a project's "New chat here…" box; stored as `eki.draft.<key>` = {"text", "attachments": [paths], "at": ms}.
// Storage may be blocked: then drafts simply aren't kept.
(function () {
  const PREFIX = "eki.draft.";
  const store = () => { try { return window.localStorage || null; } catch (e) { return null; } };

  function get(key) {
    try {
      const raw = key && store() ? store().getItem(PREFIX + key) : null;
      if (!raw) return null;
      const d = JSON.parse(raw);
      const attachments = Array.isArray(d.attachments) ? d.attachments.filter((p) => typeof p === "string" && p) : [];
      const text = typeof d.text === "string" ? d.text : "";
      return text.trim() || attachments.length ? { text, attachments, at: Number(d.at) || 0 } : null;
    } catch (e) { return null; }
  }

  function told(key) {
    try { window.dispatchEvent(new CustomEvent("eki:drafts", { detail: key })); } catch (e) { /* no events: no redraw */ }
  }

  function clear(key) {
    const had = !!get(key);
    try { if (key && store()) store().removeItem(PREFIX + key); } catch (e) { /* blocked */ }
    if (had) told(key);
  }

  function save(key, text, attachments) {
    if (!key) return;
    text = typeof text === "string" ? text : "";
    attachments = (attachments || []).filter((p) => typeof p === "string" && p);
    if (!text.trim() && !attachments.length) return clear(key);
    const had = !!get(key);
    try { store().setItem(PREFIX + key, JSON.stringify({ text, attachments, at: Date.now() })); } catch (e) { return; }
    if (!had) told(key);
  }

  // Save what a bound textarea holds now, under its key (a string, or a function for a composer whose
  // conversation changes); its pictures come from paths(), or stay what the draft already has.
  function flush(ta) {
    const b = ta && ta._ekiDraft;
    if (!b) return;
    clearTimeout(b.timer);
    b.timer = null;
    const key = typeof b.key === "function" ? b.key() : b.key;
    if (!key) return;
    const d = get(key);
    save(key, ta.value, b.paths ? b.paths() : d ? d.attachments : []);
  }

  // Restore the stored draft when the box is empty, then keep it: 400 ms after typing, at once on blur and on unload.
  // Binding the same box again only changes its key.
  function bind(ta, key, paths) {
    if (!ta) return;
    if (ta._ekiDraft) { ta._ekiDraft.key = key; ta._ekiDraft.paths = paths || null; }
    else {
      const b = ta._ekiDraft = { key, paths: paths || null, timer: null };
      ta.addEventListener("input", () => { clearTimeout(b.timer); b.timer = setTimeout(() => flush(ta), 400); });
      ta.addEventListener("blur", () => flush(ta));
      window.addEventListener("beforeunload", () => flush(ta));
    }
    const k = typeof key === "function" ? key() : key;
    const d = get(k);
    if (d && !ta.value) ta.value = d.text;
  }

  window.ekiDrafts = { bind, get, save, clear, flush, has: (threadId) => !!get(threadId) };
})();

(function () {
  const $ = (id) => document.getElementById(id);
  const EXT = { "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/heic": ".heic" };
  const items = [];   // {el, path, pending}

  const url = (p) => "/api/file?path=" + encodeURIComponent(p);

  // The server only looks at the extension; pasted pictures often have no name.
  function nameOf(f) {
    const n = f.name || "";
    return /\.\w+$/.test(n) ? encodeURIComponent(n) : "picture" + (EXT[f.type] || ".png");
  }

  function draw() { $("strip").hidden = !items.length; }

  const changed = () => ekiDrafts.flush($("prompt"));   // adding or removing a picture keeps the draft

  function remove(item, quiet) {
    const i = items.indexOf(item);
    if (i >= 0) items.splice(i, 1);
    item.el.remove();
    if (item.preview) URL.revokeObjectURL(item.preview);
    draw();
    if (!quiet) changed();
  }

  function thumb(src, item) {
    const el = document.createElement("div");
    el.className = "thumb";
    el.innerHTML = `<img alt=""><button type="button" class="x" title="Remove">✕</button>`;
    el.querySelector("img").src = src;
    el.querySelector(".x").addEventListener("click", () => remove(item));
    item.el = el;
    items.push(item);
    $("strip").appendChild(el);
    draw();
    return el;
  }

  // A draft's pictures, already uploaded: shown by reference, not sent again. One that no longer loads is a broken
  // thumb that can still be removed.
  function restore(paths) {
    items.slice().forEach((i) => remove(i, true));
    for (const p of paths || []) {
      const item = { path: p, pending: false };
      const el = thumb(url(p), item);
      el.title = p;
      el.querySelector("img").addEventListener("error", () => { el.classList.add("bad"); el.title = "Can't load: " + p; });
    }
  }

  async function add(file) {
    if (!file || !/^image\//.test(file.type || "") && !/\.(png|jpe?g|gif|webp|heic)$/i.test(file.name || "")) return;
    const preview = URL.createObjectURL(file);
    const item = { preview, path: null, pending: true };
    const el = thumb(preview, item);
    el.classList.add("pending");
    try {
      const r = await fetch("/api/attachments", {
        method: "POST", body: file,
        headers: { "X-Eki": "1", "X-Filename": nameOf(file), "Content-Type": "application/octet-stream" },
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || r.statusText);
      item.path = data.path;
      el.title = data.path;
    } catch (err) {
      el.classList.add("bad");
      el.title = "Upload failed: " + err.message;
    } finally {
      item.pending = false;
      el.classList.remove("pending");
      if (items.includes(item)) changed();
    }
  }

  const addAll = (files) => [...(files || [])].forEach(add);

  $("prompt").addEventListener("paste", (e) => {
    const files = [...(e.clipboardData ? e.clipboardData.items : [])]
      .filter((i) => i.kind === "file" && /^image\//.test(i.type)).map((i) => i.getAsFile());
    if (files.length) { e.preventDefault(); addAll(files); }
  });
  const box = $("composer");
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drop"); });
  box.addEventListener("dragleave", (e) => { if (!box.contains(e.relatedTarget)) box.classList.remove("drop"); });
  box.addEventListener("drop", (e) => {
    e.preventDefault();
    box.classList.remove("drop");
    addAll(e.dataTransfer && e.dataTransfer.files);
  });
  $("attach").addEventListener("click", () => $("attach-file").click());
  $("attach-file").addEventListener("change", (e) => { addAll(e.target.files); e.target.value = ""; });

  window.ekiAttach = {
    // Paths of the pictures uploaded so far (still-uploading and failed ones are left out).
    paths: () => items.filter((i) => i.path).map((i) => i.path),
    busy: () => items.some((i) => i.pending),
    clear: () => { items.slice().forEach((i) => remove(i)); },
    restore,
    // Small thumbnails for a user turn in the thread; a click opens the picture in the panel.
    thumbs: (paths) => (paths && paths.length) ? `<div class="thumbs">${paths.map((p) =>
      `<button type="button" class="thumb" data-file="${md.esc(p)}" title="${md.esc(p)}"><img src="${url(p)}" alt="" loading="lazy"></button>`).join("")}</div>` : "",
  };
  draw();
})();
