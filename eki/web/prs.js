// Open pull requests, as the Station and the sidebar show them. The counts come from the db
// (projects.open_prs through /api/station and /api/status); nothing here talks to GitHub.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const total = (prs) => Object.values(prs || {}).reduce((a, n) => a + n, 0);
  const plural = (n) => `${n} PR${n === 1 ? "" : "s"} open`;

  // "3 PRs open (proj-a 2, proj-b 1)", or "" when none
  function summary(prs) {
    const n = total(prs);
    return n ? `${plural(n)} (${Object.entries(prs).map(([p, k]) => `${p} ${k}`).join(", ")})` : "";
  }

  // an item's PR in place of its branch: <a target=_blank>PR #n</a>
  function link(url) {
    const m = /\/pull\/(\d+)/.exec(url || "");
    return `<a class="st-link" target="_blank" rel="noopener" href="${esc(url)}">PR #${esc(m ? m[1] : "?")}</a>`;
  }

  // the sidebar: one line per project, "<name>: N PRs open"
  function lines(prs) {
    return Object.entries(prs || {}).map(([p, k]) => `<div class="dim">${esc(p)}: ${plural(k)}</div>`).join("");
  }

  window.prs = { summary, link, lines, total };
})();
