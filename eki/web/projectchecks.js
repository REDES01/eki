// The project page's checks: under each goal in Background (project.js calls projectChecks.html(goal) per goal),
// one line per check — its id, the verdict that stands now (or —), its text cut to 60, and scope · provider · round.
// Clicking a line opens the full record under it: every verdict filed for that check, newest first, with all its
// fields, from GET /api/goals/<id>/checks (eki/api_checks.py). The lines come from the background payload's
// goals[].checks (eki/verdicts.py view); the opened records are kept here only to redraw them, never saved.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const C = { open: new Set(), recs: {} };    // open: "gid/cid"; recs: {gid: {cid: [records]}} or {gid: {error}}
  const COLOR = { passed: "var(--teal)", failed: "var(--red)", uncertain: "var(--amber)" };
  const FIELDS = [["evidence", "evidence"], ["reasoning", "reasoning"], ["counter_evidence", "counter-evidence"],
    ["limitation", "limitation"], ["suggestion", "suggestion"]];
  const cut = (t, n = 60) => { const one = String(t || "").split(/\s+/).filter(Boolean).join(" ");
    return one.length <= n ? one : one.slice(0, n - 1).trimEnd() + "…"; };
  const key = (gid, cid) => gid + "/" + cid;
  const box = (gid, cid) => "pjc-" + gid + "-" + cid;
  const when = (t) => (window.projects && window.projects.when ? window.projects.when(t) : "");

  // one check's line: `c2  passed  <text 60>  final · codex · round 1`, or `—` and no where when not judged
  function line(gid, c) {
    const v = c.verdict;
    const where = v ? [c.scope, c.provider || "?", "round " + c.round].join(" · ") : "";
    return `<div class="pj-item pj-check" data-check="${esc(c.id)}" data-check-goal="${esc(gid)}" style="padding-left:30px;cursor:pointer"
        title="Click for the full record"><span class="pj-state" style="min-width:28px">${esc(c.id)}</span>
      <span class="pj-state"${v ? ` style="color:${COLOR[v] || "var(--dim)"}"` : ""}>${esc(v || "—")}</span>
      <span class="grow">${esc(cut(c.text))}</span>${where ? `<span class="dim">${esc(where)}</span>` : ""}</div>
      <div id="${esc(box(gid, c.id))}" class="pj-check-rec">${C.open.has(key(gid, c.id)) ? records(gid, c.id) : ""}</div>`;
  }

  // every verdict for the check, newest first, each with all its fields
  function records(gid, cid) {
    const got = C.recs[gid];
    if (!got) return '<div class="dim" style="padding-left:30px">loading…</div>';
    if (got.error) return `<div class="pj-err" style="padding-left:30px">${esc(got.error)}</div>`;
    const list = got[cid] || [];
    if (!list.length) return '<div class="dim" style="padding-left:30px">Not judged yet.</div>';
    return list.map((r) => `<div class="pj-check-one" style="padding:4px 8px 6px 30px;white-space:pre-wrap">
      <div><b style="color:${COLOR[r.verdict] || "inherit"}">${esc(r.verdict)}</b>
        <span class="dim">${esc([r.scope, [r.provider, r.model].filter(Boolean).join(" ") || "?", "round " + r.round,
          when(r.created_at)].filter(Boolean).join(" · "))}</span></div>
      ${FIELDS.map(([k, label]) => `<div><span class="dim">${label}</span> ${esc(r[k] || "—")}</div>`).join("")}
      <div class="dim">run ${esc(r.run_id)}${r.item_id ? " · item " + esc(r.item_id) : ""}</div></div>`).join("");
  }

  // the lines under one goal; "" for a goal with no checks (old goals, standing rounds)
  function html(g) {
    const list = (g && g.checks) || [];
    return list.length ? list.map((c) => line(g.id, c)).join("") : "";
  }

  async function load(gid) {
    try {
      const r = await fetch("/api/goals/" + encodeURIComponent(gid) + "/checks");
      const got = await r.json().catch(() => ({}));
      if (!r.ok || got.error) throw new Error(got.error || r.statusText);
      const by = {};
      for (const c of got.checks || []) by[c.id] = c.records || [];
      C.recs[gid] = by;
    } catch (e) { C.recs[gid] = { error: e.message }; }
  }

  function draw(gid, cid) {
    const el = $(box(gid, cid));
    if (el) el.innerHTML = C.open.has(key(gid, cid)) ? records(gid, cid) : "";
  }

  async function toggle(gid, cid) {
    const k = key(gid, cid);
    if (C.open.has(k)) { C.open.delete(k); return draw(gid, cid); }
    C.open.add(k);
    draw(gid, cid);
    await load(gid);                       // fresh each open: a verdict may have come in since
    draw(gid, cid);
  }

  document.addEventListener("click", (e) => {
    const b = e.target && e.target.closest && e.target.closest("[data-check]");
    if (!b || !b.dataset.checkGoal) return;
    e.preventDefault();
    toggle(b.dataset.checkGoal, b.dataset.check);
  });

  window.projectChecks = { html, toggle, records };
})();
