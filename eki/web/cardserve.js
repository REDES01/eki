// A Preview links (serve) card's own part in an open card (projectcardpanel.js draws it under "Preview links",
// on open and on every update): one row per running copy (card.live.links, from eki/components/servelive.py) —
// the task it belongs to (or its folder's name), its address, an Open button (a link with target=_blank, so
// the Mac window opens it in its browser panel or the default browser), when it started and when it stops if
// nobody looks at it — then "your copy: :4200" (card.live.own), marked "not running" when it doesn't answer.
// Nothing here starts a server.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  if (document.querySelector && !document.querySelector('link[href="/ui/cardserve.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardserve.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  let shown = { el: null, html: "" };

  // "12 min" from seconds
  function span(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return "under a minute";
    if (s < 3600) return `${Math.round(s / 60)} min`;
    if (s < 86400) return `${Math.round(s / 3600)} h`;
    return `${Math.round(s / 86400)} d`;
  }
  const now = () => Date.now() / 1000;
  const started = (t) => t == null ? "" : now() - Number(t) < 45 ? "started just now" : `started ${span(now() - Number(t))} ago`;
  function stops(t) {
    if (t == null) return "";
    const s = Number(t) - now();
    return s <= 60 ? "stops soon if idle" : `stops in ${span(s)} if idle`;
  }
  const base = (f) => String(f || "").replace(/\/+$/, "").split("/").pop();
  const port = (url) => { const m = /^https?:\/\/[^/:]+:(\d+)/.exec(String(url || "")); return m ? m[1] : ""; };
  const addr = (url) => String(url || "").replace(/^https?:\/\//, "");

  function row(l) {
    const when = [started(l.started), stops(l.stops_at)].filter(Boolean).join(" · ");
    return `<div class="cs-row">
      <div class="cs-main"><div class="cs-task" title="${esc(l.folder)}">${esc(l.task || base(l.folder))}</div>
        <code class="cs-addr dim">${esc(addr(l.url))}</code>${when ? `<div class="cs-when dim">${esc(when)}</div>` : ""}</div>
      <a class="pj-btn cs-open" href="${esc(l.url)}" target="_blank" rel="noopener">Open</a></div>`;
  }

  function mine(own) {
    if (!own || !own.url) return '<div class="cs-own dim">your copy: not running — <code>eki use</code> this card from the project folder to start it</div>';
    const p = port(own.url);
    return `<div class="cs-own">your copy: <a href="${esc(own.url)}" target="_blank" rel="noopener">:${esc(p)}</a>` +
      (own.up ? "" : ' <span class="dim cs-down">not running</span>') + "</div>";
  }

  function html(card) {
    const live = card.live || {}, links = Array.isArray(live.links) ? live.links : [];
    const rows = links.length ? links.map(row).join("")
      : '<div class="dim cs-none">No preview links running — each agent gets one when it starts working.</div>';
    return `<div class="cs">${rows}${mine(live.own)}</div>`;
  }

  function draw(el, card) {
    const h = html(card);
    if (shown.el === el && shown.html === h && el.innerHTML) return;     // the 5 s update redraws only on change
    el.innerHTML = h;
    shown = { el, html: h };
  }

  function close() { shown = { el: null, html: "" }; }

  window.projectCardKinds.serve = { title: "Preview links", keys: [], draw, close, html };
})();
