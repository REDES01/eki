// The right side panel's width: drag its left edge (#panel-grip), double-click to reset, or arrow
// keys when the grip has focus (ArrowLeft makes it wider). Like split.js, the width is a view
// preference kept in this browser only (localStorage), written on #app as --panel-w. It is applied
// whether or not the panel is open, so opening or closing it never jumps.
(function () {
  const MIN = 320, KEY = "eki.panel.w", STEP = 16, CHAT = 380, SIDE = 272;
  const DEFAULT = "minmax(360px, 44%)";   // what the grid uses while nothing is saved
  const app = () => document.getElementById("app");

  // The sidebar's current width (its own grip writes --side-w).
  function sideW() {
    const n = parseFloat(app().style.getPropertyValue("--side-w"));
    return isFinite(n) ? n : SIDE;
  }
  // At most 70% of the window, and the chat column keeps its minimum; MIN wins when it's too narrow.
  const clamp = (w, total, side) =>
    Math.round(Math.max(MIN, Math.min(total * 0.7, total - (side == null ? SIDE : side) - CHAT, w)));

  function apply(w) {
    const n = clamp(w, window.innerWidth, sideW());
    app().style.setProperty("--panel-w", n + "px");
    return n;
  }
  function saved() {
    try { const n = parseFloat(localStorage.getItem(KEY)); return isFinite(n) ? n : null; } catch (e) { return null; }
  }
  function current() {
    const n = parseFloat(app().style.getPropertyValue("--panel-w"));
    if (isFinite(n)) return n;
    const p = document.getElementById("panel");
    return p && p.offsetWidth ? p.offsetWidth : MIN;
  }
  function set(w) {
    const n = apply(w);
    try { localStorage.setItem(KEY, String(n)); } catch (e) { /* fine */ }
    window.dispatchEvent(new Event("resize"));
    return n;
  }
  function reset() {
    app().style.removeProperty("--panel-w");
    try { localStorage.removeItem(KEY); } catch (e) { /* fine */ }
    window.dispatchEvent(new Event("resize"));
  }
  // On load and when the window changes: the saved width, clamped; no resize is re-sent.
  function restore() { const w = saved(); if (w != null) apply(w); }

  const grip = document.getElementById("panel-grip");
  if (grip) {
    let drag = false;
    grip.addEventListener("pointerdown", (e) => {
      drag = true;
      grip.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing");
      e.preventDefault();
    });
    grip.addEventListener("pointermove", (e) => { if (drag) set(window.innerWidth - e.clientX); });
    const end = () => { drag = false; document.body.classList.remove("resizing"); };
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
    grip.addEventListener("dblclick", reset);
    grip.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      set(current() + (e.key === "ArrowLeft" ? STEP : -STEP));
      e.preventDefault();
    });
  }
  window.addEventListener("resize", restore);
  restore();
  window.panelSplit = { clamp, set, reset, MIN, KEY, DEFAULT };
})();
