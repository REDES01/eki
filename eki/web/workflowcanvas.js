// A project's workflow drawn as a graph (the Workflow page, #project/<id>/workflow): the JSON of
// GET /api/projects/<pid>/workflow — its stages in pipeline order, left to right in one row, an arrow
// between each consecutive pair with a count badge when items are moving through, and the cards that
// feed a stage (knowledge, instructions, skills, serve …) as small side nodes above or below the row,
// dotted links to every stage they feed. Off stages are faded with a dashed border and still clickable;
// a locked stage carries a lock, a stage with a failure or an item left for a person a red dot.
// Plain SVG: pan by dragging empty canvas or two-finger scroll, zoom by pinch or ⌘-scroll around the
// pointer. window.workflowCanvas = { layout(data), mount(el, handlers) } — layout is pure (tests read it);
// mount draws into el and calls handlers.stage(id) / handlers.feed(feed) on a click.
(function () {
  if (typeof document !== "undefined" && document.querySelector && document.head
      && !document.querySelector('link[href="/ui/workflowcanvas.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/workflowcanvas.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const attr = (s) => esc(s).replace(/"/g, "&quot;");
  const NW = 184, NH = 64, GAP = 64;                     // a stage, and the room between two
  const FW = 132, FH = 34, FGAP = 14, FY = 58;           // a feed, the room between two, its distance from the row
  const ZMIN = 0.25, ZMAX = 2.5, PAD = 40;
  const clip = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  // Where everything goes. Stages in data order at y 0; each feed near its first target, above the row
  // first then below, pushed right past the feed before it on that side so none overlap.
  function layout(data) {
    const stages = (data && data.stages) || [];
    const nodes = stages.map((s, i) => ({ id: s.id, x: i * (NW + GAP), y: 0, w: NW, h: NH }));
    const at = {};
    nodes.forEach((n, i) => { at[n.id] = i; });
    const edges = [];
    for (let i = 1; i < stages.length; i++)
      edges.push({ from: stages[i - 1].id, to: stages[i].id, count: stages[i].count || 0 });
    const list = ((data && data.feeds) || []).map((f, i) => {
      const t = (f.to || []).filter((id) => id in at);
      return { i, t, first: t.length ? at[t[0]] : 0 };
    });
    const order = list.slice().sort((a, b) => a.first - b.first || a.i - b.i);
    const right = [-Infinity, -Infinity], per = {};
    const feeds = [];
    for (const f of order) {
      const k = per[f.first] = (per[f.first] || 0) + 1;
      let side = (k - 1) % 2;
      const n = nodes[f.first] || { x: 0, w: NW };
      const want = n.x + n.w / 2 - FW / 2;
      if (want < right[side] + FGAP && right[1 - side] + FGAP <= want) side = 1 - side;
      const x = Math.max(want, right[side] + FGAP);
      right[side] = x + FW;
      feeds[f.i] = { i: f.i, x, y: side ? NH + FY : -FY - FH, w: FW, h: FH };
    }
    const links = [];
    list.forEach((f) => f.t.forEach((to, k) =>                         // the note once, on the first link
      links.push({ feed: f.i, to, note: k ? "" : data.feeds[f.i].note || "" })));
    return { nodes, feeds, edges, links };
  }

  function bounds(L) {
    const all = L.nodes.concat(L.feeds.filter(Boolean));
    if (!all.length) return { x: 0, y: 0, w: 1, h: 1 };
    const x0 = Math.min(...all.map((b) => b.x)), y0 = Math.min(...all.map((b) => b.y));
    const x1 = Math.max(...all.map((b) => b.x + b.w)), y1 = Math.max(...all.map((b) => b.y + b.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  // ---- the markup ----------------------------------------------------------------------------------

  function node(s, n, selected) {
    const cls = "wf-node" + (s.on === false ? " wf-off" : "") + (s.id === selected ? " wf-sel" : "")
      + (s.locked ? " wf-locked" : "") + (s.alert ? " wf-alert" : "");
    const tip = [s.title, s.summary, s.what].filter(Boolean).join(" — ");
    return `<g class="${cls}" data-stage="${attr(s.id)}" transform="translate(${n.x},${n.y})">
      <title>${esc(tip)}</title>
      <rect class="wf-box" width="${n.w}" height="${n.h}" rx="9"></rect>
      <text class="wf-title" x="14" y="26">${esc(clip(s.title, 20))}</text>
      <text class="wf-sum" x="14" y="47">${esc(clip(s.summary, 28))}</text>
      ${s.locked ? `<text class="wf-lock" x="${n.w - 14}" y="25" text-anchor="end">🔒</text>` : ""}
      ${s.count > 0 ? `<text class="wf-n" x="${n.w - 14}" y="47" text-anchor="end">${esc(s.count)}</text>` : ""}
      ${s.alert ? `<circle class="wf-dot" cx="${n.w - 2}" cy="2" r="6"></circle>` : ""}
    </g>`;
  }

  function edge(e, a, b) {
    const x0 = a.x + a.w, x1 = b.x - 3, y = a.y + a.h / 2, mx = (x0 + x1) / 2;
    const badge = e.count > 0
      ? `<g class="wf-badge" transform="translate(${mx},${y - 14})"><rect x="-13" y="-10" width="26" height="18" rx="9"></rect>
         <text y="3" text-anchor="middle">${esc(e.count)}</text></g>` : "";
    return `<g class="wf-edge" data-from="${attr(e.from)}" data-to="${attr(e.to)}">
      <path d="M${x0},${y} L${x1},${y}" marker-end="url(#wf-arrow)"></path>${badge}</g>`;
  }

  function link(l, f, n) {
    const above = f.y < n.y;
    const x0 = f.x + f.w / 2, y0 = above ? f.y + f.h : f.y;
    const x1 = Math.min(n.x + n.w - 24, Math.max(n.x + 24, x0));          // straight down when it can
    const y1 = above ? n.y : n.y + n.h, my = (y0 + y1) / 2;
    const note = l.note ? `<text class="wf-note" x="${(x0 + x1) / 2 + 6}" y="${my + 4}">${esc(l.note)}</text>` : "";
    return `<g class="wf-link" data-feed="${l.feed}" data-to="${attr(l.to)}">
      <path d="M${x0},${y0} C${x0},${my} ${x1},${my} ${x1},${y1}"></path>${note}</g>`;
  }

  function feed(f, b) {
    const tip = [f.label || f.card, f.kind, f.note].filter(Boolean).join(" · ");
    return `<g class="wf-feed" data-feed="${b.i}" transform="translate(${b.x},${b.y})">
      <title>${esc(tip)}</title>
      <rect class="wf-fbox" width="${b.w}" height="${b.h}" rx="7"></rect>
      <text class="wf-fkind" x="10" y="14">${esc(clip(f.kind, 18))}</text>
      <text class="wf-flabel" x="10" y="27">${esc(clip(f.label || f.card, 18))}</text>
    </g>`;
  }

  function markup(data, L, selected, view) {
    const stages = (data && data.stages) || [], fs = (data && data.feeds) || [];
    const byId = {};
    L.nodes.forEach((n) => { byId[n.id] = n; });
    const links = L.links.map((l) => link(l, L.feeds[l.feed], byId[l.to])).join("");
    const edges = L.edges.map((e) => edge(e, byId[e.from], byId[e.to])).join("");
    const nodes = stages.map((s, i) => node(s, L.nodes[i], selected)).join("");
    const feeds = fs.map((f, i) => (L.feeds[i] ? feed(f, L.feeds[i]) : "")).join("");
    return `<svg class="wf-svg" xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">
      <defs><marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z"></path></marker></defs>
      <g class="wf-world" transform="${tf(view)}">${links}${edges}${feeds}${nodes}</g></svg>`;
  }

  const tf = (v) => `translate(${v.tx},${v.ty}) scale(${v.k})`;
  const clamp = (k) => Math.min(ZMAX, Math.max(ZMIN, k));

  // ---- mounted: draw, pan, zoom, click ---------------------------------------------------------------

  function mount(el, handlers) {
    handlers = handlers || {};
    const S = { data: null, L: null, selected: null, view: { k: 1, tx: 0, ty: 0 }, drawn: false, drag: null, moved: false };
    if (el.classList) el.classList.add("wf-canvas");
    const size = () => ({ w: el.clientWidth || 800, h: el.clientHeight || 500 });
    const apply = () => { const w = el.querySelector && el.querySelector(".wf-world"); if (w) w.setAttribute("transform", tf(S.view)); };
    const local = (ev) => {
      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0 };
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };

    function fit() {
      if (!S.L) return;
      const b = bounds(S.L), { w, h } = size();
      const k = clamp(Math.min((w - 2 * PAD) / b.w, (h - 2 * PAD) / b.h, 1.2));
      S.view = { k, tx: (w - b.w * k) / 2 - b.x * k, ty: (h - b.h * k) / 2 - b.y * k };
      apply();
    }

    function draw(data, selected) {
      S.data = data; S.selected = selected == null ? null : selected;
      S.L = layout(data);
      if (!S.drawn) {
        S.drawn = true;
        el.innerHTML = markup(data, S.L, S.selected, S.view);
        fit();
        return;
      }
      el.innerHTML = markup(data, S.L, S.selected, S.view);
    }

    function zoomAt(p, factor) {
      const v = S.view, k = clamp(v.k * factor);
      S.view = { k, tx: p.x - (p.x - v.tx) * (k / v.k), ty: p.y - (p.y - v.ty) * (k / v.k) };
      apply();
    }

    const hit = (t, sel) => (t && t.closest ? t.closest(sel) : null);
    const on = {
      click(ev) {
        if (S.moved) { S.moved = false; return; }
        const st = hit(ev.target, "[data-stage]");
        if (st) { if (handlers.stage) handlers.stage(st.getAttribute ? st.getAttribute("data-stage") : st.dataset.stage); return; }
        const fd = hit(ev.target, ".wf-feed[data-feed], .wf-link[data-feed]");
        if (fd && S.data) {
          const i = Number(fd.getAttribute ? fd.getAttribute("data-feed") : fd.dataset.feed);
          if (handlers.feed && S.data.feeds && S.data.feeds[i]) handlers.feed(S.data.feeds[i]);
        }
      },
      pointerdown(ev) {
        if (ev.button !== 0 || hit(ev.target, "[data-stage], [data-feed]")) return;
        S.drag = { x: ev.clientX, y: ev.clientY, tx: S.view.tx, ty: S.view.ty, id: ev.pointerId };
        S.moved = false;
        if (el.setPointerCapture && ev.pointerId != null) try { el.setPointerCapture(ev.pointerId); } catch (e) { /* gone */ }
        if (el.classList) el.classList.add("wf-panning");
      },
      pointermove(ev) {
        const d = S.drag;
        if (!d) return;
        const dx = ev.clientX - d.x, dy = ev.clientY - d.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) S.moved = true;
        S.view = { k: S.view.k, tx: d.tx + dx, ty: d.ty + dy };
        apply();
      },
      pointerup() { S.drag = null; if (el.classList) el.classList.remove("wf-panning"); },
      wheel(ev) {
        if (ev.preventDefault) ev.preventDefault();
        if (ev.ctrlKey || ev.metaKey) { zoomAt(local(ev), Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.002))); return; }
        S.view = { k: S.view.k, tx: S.view.tx - ev.deltaX, ty: S.view.ty - ev.deltaY };
        apply();
      },
    };
    on.pointercancel = on.pointerup;
    for (const [ev, fn] of Object.entries(on)) el.addEventListener(ev, fn, ev === "wheel" ? { passive: false } : undefined);

    function unmount() {
      for (const [ev, fn] of Object.entries(on)) if (el.removeEventListener) el.removeEventListener(ev, fn);
      if (el.classList) el.classList.remove("wf-canvas", "wf-panning");
      el.innerHTML = "";
      S.drawn = false;
    }

    return { draw, fit, unmount, view: () => ({ ...S.view }) };
  }

  window.workflowCanvas = { layout, mount };
})();
