// Pictures in the composer: paste, drop, or 📎. Each is uploaded at once
// (POST /api/attachments) and shown in a strip above the textarea; app.js
// sends ekiAttach.paths() with the ask and then calls ekiAttach.clear().
// Only the unsent strip lives here, so a reload loses nothing the engine has.
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

  function remove(item) {
    const i = items.indexOf(item);
    if (i >= 0) items.splice(i, 1);
    item.el.remove();
    URL.revokeObjectURL(item.preview);
    draw();
  }

  async function add(file) {
    if (!file || !/^image\//.test(file.type || "") && !/\.(png|jpe?g|gif|webp|heic)$/i.test(file.name || "")) return;
    const el = document.createElement("div");
    el.className = "thumb pending";
    const preview = URL.createObjectURL(file);
    el.innerHTML = `<img alt=""><button type="button" class="x" title="Remove">✕</button>`;
    el.querySelector("img").src = preview;
    const item = { el, preview, path: null, pending: true };
    el.querySelector(".x").addEventListener("click", () => remove(item));
    items.push(item);
    $("strip").appendChild(el);
    draw();
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
    clear: () => { items.slice().forEach(remove); },
    // Small thumbnails for a user turn in the thread; a click opens the picture in the panel.
    thumbs: (paths) => (paths && paths.length) ? `<div class="thumbs">${paths.map((p) =>
      `<button type="button" class="thumb" data-file="${md.esc(p)}" title="${md.esc(p)}"><img src="${url(p)}" alt="" loading="lazy"></button>`).join("")}</div>` : "",
  };
  draw();
})();
