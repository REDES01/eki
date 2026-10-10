// What the Mac shell adds to the page: a folder panel. Elsewhere every call
// answers at once with "can't", so a page works the same without the shell.
(function () {
  const pending = new Map();
  let seq = 0;

  function handler() {
    const w = window.webkit;
    return (w && w.messageHandlers && w.messageHandlers.eki) || null;
  }

  function canPickFolder() {
    return !!handler() && /EkiMac/.test((window.navigator || {}).userAgent || "");
  }

  // Resolves with the chosen folder's path, or null when cancelled or not in the shell.
  function pickFolder() {
    if (!canPickFolder()) return Promise.resolve(null);
    const id = "f" + (++seq) + "-" + Date.now().toString(36);
    return new Promise((resolve) => {
      pending.set(id, resolve);
      try { handler().postMessage({ pickFolder: id }); }
      catch (e) { pending.delete(id); resolve(null); }
    });
  }

  // The shell's answer.
  function picked(id, path) {
    const resolve = pending.get(id);
    if (!resolve) return;
    pending.delete(id);
    resolve(typeof path === "string" && path ? path : null);
  }

  // the shell may already have put its build's commit here (shellbar.js reads it): keep it
  window.ekiShell = Object.assign(window.ekiShell || {}, { canPickFolder, pickFolder, picked });
})();
