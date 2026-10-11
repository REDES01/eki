// The side panel: a file beside the thread. Text docs (.md/.markdown/.txt) go to docview.js; the rest show here.
(function () {
  const $ = (id) => document.getElementById(id);
  const IMG = /\.(png|jpe?g|gif|webp|svg)$/i, PAGE = /\.(html?|pdf)$/i, DOC = /\.(md|markdown|txt)$/i;

  function reveal() {
    $("panel").hidden = false;
    document.getElementById("app").classList.add("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  async function show(path) {
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
    if (window.docview) { await docview.beforeClose(); docview.hide(); }
    $("panel").hidden = true;
    $("panel-body").innerHTML = "";
    document.getElementById("app").classList.remove("with-panel");
    window.dispatchEvent(new Event("resize"));
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
    if (f) { if (e.preventDefault) e.preventDefault(); show(f.dataset.file); }
  });
  $("panel-close").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("panel").hidden) close(); });
  window.panel = { show, close, reveal };
})();
