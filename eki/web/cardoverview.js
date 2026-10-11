// The three plain sections every open card starts with (projectcardpanel.js draws them first, before any
// settings), from the card's server-built text (eki/cardview.py): "What it does" — card.explain, the kind's
// paragraph, then the card's own about line; "Used by" — card.overview.used_by: its line, the workflow
// stages as chips and up to 5 recent runs with how long ago; "Right now" — each line of card.overview.now.
// A key an older server lacks draws an empty section, never an error.
(function () {
  if (document.querySelector && !document.querySelector('link[href="/ui/cardoverview.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardoverview.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const list = (v) => Array.isArray(v) ? v : [];
  const none = '<div class="dim co-none">—</div>';

  function what(card) {
    const out = (card.explain ? `<p class="co-explain">${esc(card.explain)}</p>` : "") +
      (card.about ? `<p class="co-about">${esc(card.about)}</p>` : "");
    return out || none;
  }

  function usedBy(card, ago) {
    const u = (card.overview && card.overview.used_by) || {};
    const stages = list(u.stages), runs = list(u.runs).slice(0, 5);
    const out = (u.line ? `<div class="co-line">${esc(u.line)}</div>` : "") +
      (stages.length ? `<div class="co-chips">${stages.map((s) => `<span class="co-chip">${esc(s)}</span>`).join("")}</div>` : "") +
      (runs.length ? `<ul class="co-runs">${runs.map((r) => `<li class="co-run" title="${esc(r.run)}">
          <span class="co-rtitle">${esc(r.title || r.run || "")}</span>
          <span class="dim co-when">${esc(r.when ? ago(r.when) : "")}</span></li>`).join("")}</ul>` : "");
    return out || '<div class="dim co-none">Not used yet.</div>';
  }

  function now(card) {
    const lines = list(card.overview && card.overview.now);
    return lines.length ? lines.map((l) => `<div class="co-now">${esc(l)}</div>`).join("") : none;
  }

  // the three sections' html; ago turns epoch seconds into "12 min ago"
  function html(card, ago) {
    const a = typeof ago === "function" ? ago : () => "";
    return `<div class="co">
      <h4 class="pp-h co-h">What it does</h4><div class="co-what">${what(card)}</div>
      <h4 class="pp-h co-h">Used by</h4><div class="co-used">${usedBy(card, a)}</div>
      <h4 class="pp-h co-h">Right now</h4><div class="co-right">${now(card)}</div></div>`;
  }

  window.cardOverview = { html };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
