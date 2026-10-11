// The side panel: a file beside the thread, or a hosted view. Text docs (.md/.markdown/.txt) go to docview.js;
// the rest show here. panel.host(owner) lends the panel to a non-file view (a card's details):
// owner = { name, sub, draw(body), close(why) } with why = "swap" | "show" | "back" | "close".
// A file opened from inside a hosted view gets "← back to <owner>" (#panel-back), which hosts it again.
(function () {
  const $ = (id) => document.getElementById(id);
  const IMG = /\.(png|jpe?g|gif|webp|svg)$/i, PAGE = /\.(html?|pdf)$/i, DOC = /\.(md|markdown|txt)$/i;
  let hosted = null, backTo = null;

  function reveal() {
    $("panel").hidden = false;
    document.getElementById("app").classList.add("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  function release(why) {
    const o = hosted;
    hosted = null;
    if (o) o.close(why);
    return o;
  }

  function setBack(o) {
    backTo = o;
    const b = $("panel-back");
    b.hidden = !o;
    b.textContent = o ? "← back to " + o.name : "";
  }

  async function host(owner) {
    if (window.docview) { await docview.beforeClose(); docview.hide(); }
    if (hosted && hosted !== owner) release("swap");
    hosted = owner;
    $("panel-name").textContent = owner.name;
    $("panel-path").textContent = owner.sub || "";
    $("panel-open").hidden = true;
    setBack(null);
    const body = $("panel-body");
    body.innerHTML = "";
    reveal();
    owner.draw(body);
  }

  function retitle(name, sub) {
    if (!hosted) return;
    $("panel-name").textContent = name;
    $("panel-path").textContent = sub || "";
  }

  async function show(path, opts) {
    const back = !!(opts && opts.back);
    const o = release(back ? "back" : "show");
    setBack(back ? o : null);
    $("panel-open").hidden = false;
    if (DOC.test(path) && window.docview) return docview.show(path);
    if (window.docview) { await docview.beforeClose(); docview.hide(); }
    const url = "/api/file?path=" + encodeURIComponent(path);
    $("panel-name").textContent = path.split("/").pop();
    $("panel-path").textContent = path;
    $("panel-open").href = url;
    const body = $("panel-body");
    if (IMG.test(path)) body.innerHTML = `<img src="${url}&t=${Date.now()}" alt="">`;
    else if (PAGE.test(path)) body.innerHTML = `<iframe src="${url}" sandbox="allow-scripts allow-popups"></iframe>`;
    else {
      const r = await fetch(url);
      const text = r.ok ? await r.text() : "Can't show this file.";
      body.innerHTML = `<pre class="pad">${md.esc(text)}</pre>`;
    }
    reveal();
  }

  async function close() {
    release("close");
    setBack(null);
    if (window.docview) { await docview.beforeClose(); docview.hide(); }
    $("panel").hidden = true;
    $("panel-body").innerHTML = "";
    document.getElementById("app").classList.remove("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  // Esc in a field of the panel only leaves the field (so its change/focusout saves fire); the next Esc closes
  function onKey(e) {
    if (e.key !== "Escape" || $("panel").hidden) return;
    const f = document.activeElement;
    if (f && /^(INPUT|TEXTAREA|SELECT)$/.test(f.tagName || "") && $("panel-body").contains(f)) { f.blur(); return; }
    close();
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("#panel [data-anchor]");
    if (a) {
      e.preventDefault();
      const h = document.getElementById(a.dataset.anchor);
      if (h) h.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    const f = e.target.closest("[data-file]");
    if (!f) return;
    if (e.preventDefault) e.preventDefault();
    const inside = hosted && $("panel-body").contains(f);
    show(f.dataset.file, inside ? { back: true } : undefined);
  });
  $("panel-close").addEventListener("click", close);
  $("panel-back").addEventListener("click", (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (backTo) host(backTo);
  });
  document.addEventListener("keydown", onKey);
  window.panel = { show, close, reveal, host, retitle, owner: () => hosted };
})();
