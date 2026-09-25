// The side panel: a file an agent made, shown beside the thread.
(function () {
  const $ = (id) => document.getElementById(id);
  const IMG = /\.(png|jpe?g|gif|webp|svg)$/i, PAGE = /\.(html?|pdf)$/i, MD = /\.md$/i;

  async function show(path) {
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
      body.innerHTML = MD.test(path) ? `<div class="answer pad">${md.render(text)}</div>` : `<pre class="pad">${md.esc(text)}</pre>`;
    }
    $("panel").hidden = false;
    document.getElementById("app").classList.add("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  function close() {
    $("panel").hidden = true;
    $("panel-body").innerHTML = "";
    document.getElementById("app").classList.remove("with-panel");
    window.dispatchEvent(new Event("resize"));
  }

  document.addEventListener("click", (e) => {
    const f = e.target.closest("[data-file]");
    if (f) show(f.dataset.file);
  });
  $("panel-close").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("panel").hidden) close(); });
  window.panel = { show, close };
})();
