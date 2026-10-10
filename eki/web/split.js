// The sidebar's width: drag the line between it and the main area (#side-grip), double-click
// to reset, or arrow keys when the grip has focus. The width is a view preference kept in this
// browser only (localStorage, like eki.station.open), written on #app as --side-w.
(function () {
  const MIN = 200, MAX = 480, DEFAULT = 272, KEY = "eki.side.w", STEP = 16;
  const app = () => document.getElementById("app");

  // At least 420px is left for the main area.
  const clamp = (w, total) => Math.round(Math.max(MIN, Math.min(MAX, total - 420, w)));

  function apply(w) {
    const n = clamp(w, window.innerWidth);
    app().style.setProperty("--side-w", n + "px");
    return n;
  }
  function saved() {
    try { const n = parseFloat(localStorage.getItem(KEY)); return isFinite(n) ? n : null; } catch (e) { return null; }
  }
  function current() {
    const n = parseFloat(app().style.getPropertyValue("--side-w"));
    return isFinite(n) ? n : DEFAULT;
  }
  function set(w) {
    const n = apply(w);
    try { localStorage.setItem(KEY, String(n)); } catch (e) { /* fine */ }
    window.dispatchEvent(new Event("resize"));
    return n;
  }
  function reset() {
    app().style.removeProperty("--side-w");
    try { localStorage.removeItem(KEY); } catch (e) { /* fine */ }
    window.dispatchEvent(new Event("resize"));
  }
  // On load and when the window changes: the saved width, clamped; no resize is re-sent.
  function restore() { const w = saved(); if (w != null) apply(w); }

  const grip = document.getElementById("side-grip");
  if (grip) {
    let drag = false;
    grip.addEventListener("pointerdown", (e) => {
      drag = true;
      grip.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing");
      e.preventDefault();
    });
    grip.addEventListener("pointermove", (e) => { if (drag) set(e.clientX); });
    const end = () => { drag = false; document.body.classList.remove("resizing"); };
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
    grip.addEventListener("dblclick", reset);
    grip.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      set(current() + (e.key === "ArrowLeft" ? -STEP : STEP));
      e.preventDefault();
    });
  }
  window.addEventListener("resize", restore);
  restore();
  window.split = { clamp, set, reset, MIN, MAX, DEFAULT, KEY };
})();
