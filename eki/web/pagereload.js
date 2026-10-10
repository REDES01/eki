// When a new build of eki goes live, this page keeps running the old build's scripts against the new engine.
// It asks /api/status which build serves it and, when that changes, reloads itself (the same chat stays open) —
// at once if you're not in the middle of something; otherwise a small bar offers Refresh, so nothing you're
// typing is lost.
(function () {
  let mine = null, bar = null;

  function busy() {
    const p = document.getElementById("prompt");
    if (p && p.value.trim()) return true;
    if (document.querySelector(".tmenu, dialog[open]")) return true;
    const a = document.activeElement;
    if (a && (a.isContentEditable || ((a.tagName === "INPUT" || a.tagName === "TEXTAREA") && a.value))) return true;
    return false;
  }

  function show() {
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "pagebar";
      bar.innerHTML = '<span>eki was updated</span><span class="dim">·</span><button type="button" class="ghost small">Refresh</button>';
      bar.querySelector("button").addEventListener("click", () => location.reload());
      document.body.appendChild(bar);
    }
    bar.hidden = false;
  }

  async function poll() {
    try {
      const r = await fetch("/api/status");
      if (!r.ok) return;
      const b = (await r.json()).build;
      if (!b) return;
      if (mine === null) { mine = b; return; }
      if (b === mine) return;
      if (busy()) show(); else location.reload();
    } catch (e) { /* the engine is switching builds: next poll */ }
  }

  poll();
  setInterval(poll, 15000);
  window.addEventListener("focus", poll);
  window.pagereload = { busy };
})();
