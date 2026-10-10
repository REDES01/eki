// The Mac window's own update. When eki has rebuilt Eki.app (shellbuild.py) the
// window that is open still runs the old binary; the shell tells the page which
// commit it was built from (window.ekiShell) and /api/shell says which one is
// installed. If they differ, a small bar offers Restart. Only a click restarts:
// the poll below just shows or hides the bar.
(function () {
  if (document.documentElement.dataset.shell !== "mac" || !window.ekiShell) return;
  let bar = null;

  // the shell's reply to {restart}: it waits for itself to quit, then opens the new app here
  function restart() {
    const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.eki;
    if (h) h.postMessage({ restart: location.href });
  }

  function make() {
    bar = document.createElement("div");
    bar.id = "shellbar";
    bar.hidden = true;
    bar.innerHTML = `<span>eki's window has an update</span><span class="dim">·</span><button type="button" class="ghost small">Restart</button>`;
    bar.querySelector("button").addEventListener("click", restart);
    document.body.appendChild(bar);
  }

  // shown when an app is installed, it isn't this window's, and no rebuild is under way
  function outdated(s) {
    return !!(s && s.installed && s.installed !== window.ekiShell.commit && s.state !== "building");
  }

  function poll() {
    fetch("/api/shell").then((r) => r.ok ? r.json() : null).then((s) => {
      if (!bar) make();
      bar.hidden = !outdated(s);
    }).catch(() => {});
  }

  poll();
  setInterval(poll, 60000);
  window.addEventListener("focus", poll);
  window.shellbar = { outdated };
})();
