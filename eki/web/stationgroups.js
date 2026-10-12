// Station: the goals by project (selfview.groups) — eki itself first, then each project by name.
// Each group has a header and its own "show N older goals" fold; a goal renders through the
// station page's own goal() so states, notes, asks and buttons are the same everywhere.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ENDS = ["dropped", "applied", "landed", "live"];
  const DONE_SHOWN = 3;                                                             // finished goals shown before "show all"
  const busy = (g) => g.items.some((it) => !ENDS.includes(it.state)) || ["drafting", "planning", "failed"].includes(g.state) || g.drafting || g.error;
  const key = (grp) => grp.project == null ? "eki" : grp.project;

  // h: {goal(g) → html, btn(act, label, data, extra) → html, all: Set of group keys shown in full}
  function group(grp, h) {
    const k = key(grp), all = h.all.has(k);
    let done = 0;
    const shown = all ? grp.goals : grp.goals.filter((g) => busy(g) || done++ < DONE_SHOWN);
    const hidden = grp.goals.length - shown.length;
    const items = grp.goals.flatMap((g) => g.items);
    const yours = grp.goals.filter((g) => g.stands === "for you").length;
    const flight = grp.goals.filter((g) => g.stands === "in flight").length;
    const sums = [yours ? `${yours} for you` : "", flight ? `${flight} in flight` : "",
      `${items.length} item${items.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
    const more = hidden > 0 ? `<div class="st-line">${h.btn("goals-all", `show ${hidden} older goal${hidden === 1 ? "" : "s"}`, k, "small")}</div>`
      : (all && grp.goals.length > DONE_SHOWN ? `<div class="st-line">${h.btn("goals-few", "show fewer", k, "small")}</div>` : "");
    return `<div class="st-group" data-project="${esc(k)}"><div class="st-group-h"><b>${esc(grp.name)}</b>
        <span class="dim">${esc(sums)}</span></div>${shown.map(h.goal).join("")}${more}</div>`;
  }

  function render(groups, h) {
    if (!groups || !groups.length) return '<div class="dim">nothing yet — the box above asks for a change</div>';
    return groups.map((grp) => group(grp, h)).join("");
  }

  window.stationGroups = { render, key };
})();
