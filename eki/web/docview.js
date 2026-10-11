// The doc viewer in the side panel: a local .md/.txt rendered, its outline, its last change; live while open.
// panel.js sends text docs here; Edit hands the body to doceditor.js and comes back on Done.
(function () {
  const $ = (id) => document.getElementById(id);
  const S = { path: null, doc: null, editing: false, timer: null, seq: 0 };
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const TXT = /\.txt$/i;

  // /Users/<name>/… shown as ~/…
  const tilde = (p) => String(p || "").replace(/^(\/Users\/[^/]+|\/home\/[^/]+)(?=\/|$)/, "~");
  const folder = (p) => String(p).replace(/\/[^/]*$/, "") || "/";

  function ago(t) {
    const s = Math.max(0, Date.now() / 1000 - (t || 0));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    if (s < 7 * 86400) return Math.floor(s / 86400) + " d ago";
    return "on " + new Date(t * 1000).toLocaleDateString();
  }

  function meta(doc) {
    const w = doc.writer;
    const by = !w ? "" : w.by === "run" ? ` by an agent run <code>${esc(w.run)}</code>` : " by you";
    return `edited ${ago(doc.mtime)}${by}`;
  }

  const slug = (s) => "h-" + s.toLowerCase().replace(/[`*_[\]()]/g, "").trim().replace(/[^\w\- ]+/g, "").replace(/\s+/g, "-");

  // md.outline when md.js has it; else the headings outside code fences, the same ids md.js gives them
  function outline(text) {
    if (md.outline) return md.outline(text) || [];
    const out = [];
    let fenced = false;
    for (const line of String(text || "").split("\n")) {
      if (/^```/.test(line)) { fenced = !fenced; continue; }
      const h = !fenced && line.match(/^(#{1,6})\s+(.*)/);
      if (h) out.push({ level: h[1].length, text: h[2].trim(), id: slug(h[2]) });
    }
    return out;
  }

  function outlineHtml(text) {
    const hs = outline(text);
    if (!hs.length) return "";
    const top = Math.min(...hs.map((h) => h.level || h.depth || 1));
    const rows = hs.map((h) => {
      const lv = (h.level || h.depth || 1) - top;
      return `<li style="padding-left:${lv * 12}px"><a href="#" data-anchor="${esc(h.id)}">${esc(h.text || h.title || "")}</a></li>`;
    }).join("");
    return `<details class="doc-outline" open><summary>Outline <span class="dim">${hs.length}</span></summary><ul>${rows}</ul></details>`;
  }

  function bodyHtml(doc) {
    const edit = doc.editable && window.doceditor
      ? '<button type="button" class="ghost small doc-edit">Edit</button>'
      : !doc.editable && doc.reason ? `<span class="dim doc-ro">${esc(doc.reason)}</span>` : "";
    const content = TXT.test(doc.path) ? `<pre class="doc-txt">${esc(doc.text)}</pre>` : md.render(doc.text || "", { base: folder(doc.path) });
    return `<div class="doc-meta"><span class="dim">${meta(doc)}</span><span class="grow"></span>${edit}</div>` +
      (TXT.test(doc.path) ? "" : outlineHtml(doc.text)) +
      `<div class="answer pad doc">${content}</div>`;
  }

  function head(doc) {
    $("panel-name").textContent = doc.name || doc.path.split("/").pop();
    $("panel-path").textContent = tilde(doc.path);
    $("panel-open").href = "/api/file?path=" + encodeURIComponent(doc.path);
  }

  function draw(keep) {
    const body = $("panel-body"), top = body.scrollTop || 0;
    body.innerHTML = bodyHtml(S.doc);
    const b = body.querySelector && body.querySelector(".doc-edit");
    if (b) b.addEventListener("click", edit);
    if (keep) body.scrollTop = top;
  }

  const get = (path) => fetch("/api/doc?path=" + encodeURIComponent(path), { cache: "no-store" });

  async function show(path) {
    if (S.editing) await beforeClose();
    stop();
    const seq = ++S.seq;
    S.path = path; S.doc = null; S.editing = false;
    $("panel-name").textContent = path.split("/").pop();
    $("panel-path").textContent = tilde(path);
    $("panel-open").href = "/api/file?path=" + encodeURIComponent(path);
    const body = $("panel-body");
    if (window.panel && panel.reveal) panel.reveal();
    let r, got;
    try { r = await get(path); got = await r.json(); } catch (e) { got = { error: "Can't reach eki." }; r = { ok: false }; }
    if (seq !== S.seq) return;
    if (!r.ok || got.error) {
      body.innerHTML = `<div class="pad dim doc-err">${esc(got.error || "Can't show this file.")}</div>`;
      return;
    }
    S.doc = got;
    head(got);
    body.scrollTop = 0;
    draw(false);
    window.dispatchEvent(new CustomEvent("eki:doc", { detail: { path: got.path || path } }));
    S.timer = setInterval(poll, 3000);
  }

  // live: a doc shown, not being edited, changed on disk → draw it again where the reader was
  async function poll() {
    if (!S.doc || S.editing || (typeof document.hidden === "boolean" && document.hidden)) return;
    const seq = S.seq;
    try {
      const r = await get(S.path);
      if (!r.ok) return;
      const got = await r.json();
      if (seq !== S.seq || S.editing || !got || got.error || got.hash === S.doc.hash) return;
      S.doc = got;
      head(got);
      draw(true);
    } catch (e) { /* offline a moment; the next poll tries again */ }
  }

  function stop() { if (S.timer) clearInterval(S.timer); S.timer = null; }

  function edit() {
    if (!S.doc || !window.doceditor) return;
    stop();
    S.editing = true;
    const seq = S.seq;
    doceditor.open($("panel-body"), S.doc, (latest) => {
      if (seq !== S.seq) return;
      const body = $("panel-body");
      const ta = body.querySelector && body.querySelector("textarea");
      const sc = ta && ta.scrollHeight > ta.clientHeight ? ta : body;
      const ratio = sc.scrollHeight > sc.clientHeight ? sc.scrollTop / (sc.scrollHeight - sc.clientHeight) : 0;
      S.editing = false;
      if (latest) S.doc = Object.assign({}, S.doc, latest);
      head(S.doc);
      draw(false);
      body.scrollTop = ratio * Math.max(0, body.scrollHeight - body.clientHeight);
      S.timer = setInterval(poll, 3000);
    });
  }

  // before the panel closes or shows something else: save what's being typed
  function beforeClose() {
    if (S.editing && window.doceditor && doceditor.flush) return Promise.resolve(doceditor.flush()).catch(() => {});
    return Promise.resolve();
  }

  // the panel moved on (closed, or a picture/page shown)
  function hide() { stop(); S.seq++; S.path = null; S.doc = null; S.editing = false; }

  window.docview = { show, beforeClose, hide, current: () => S.path, poll };
})();
