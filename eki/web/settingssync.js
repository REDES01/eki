// Settings → Claude sync, read-only: what each direction of the sync with the Claude client last
// did (GET /api/sync, eki/api_sync.py). One folded line per direction — name, last run, count,
// clashes — that opens to its note and its clashes, one open at a time; then the drift between
// ~/.claude/CLAUDE.md and eki's AGENTS.md in a pre block when there is one. It edits no file:
// housekeeping runs the directions, `eki sync` shows the same.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { got: null, err: null, busy: false, at: 0, open: null };
  const STALE = 10000;   // read again when the section is drawn and this is older (ms)

  async function load() {
    S.busy = true;
    try {
      const r = await fetch("/api/sync");
      const got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
      [S.got, S.err] = [got, null];
    } catch (e) {
      S.err = e.message;
    } finally {
      [S.busy, S.at] = [false, Date.now()];
    }
    if (settings.on() && location.hash.startsWith("#settings/sync")) settings.render();
  }

  function ago(t) {
    if (!t) return "never";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    for (const [u, n] of [["d", 86400], ["h", 3600], ["m", 60]]) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
    return `${s}s ago`;
  }

  const clashWord = (n) => n ? `${n} clash${n === 1 ? "" : "es"}` : "no clashes";

  function body(d) {
    const line = settingsForm.line, block = settingsForm.block;
    return settingsForm.lines("",
      (d.note ? line("Note", esc(d.note)) : "") +
      (d.clashes.length ? block("Clashes", d.clashes.map((c) => `<div class="set-amber">${esc(c)}</div>`).join(""))
        : line("Clashes", '<span class="dim">none</span>')));
  }

  function fold(d) {
    const n = d.clashes.length;
    const sum = `<b>${esc(d.name)}</b> <span class="dim">${esc(ago(d.at))}</span> <span class="dim">${esc(d.count)}</span> ` +
      `<span class="${n ? "set-amber" : "dim"}">${esc(clashWord(n))}</span>`;
    const open = S.open === d.name;
    return settingsForm.fold(d.name, sum, open ? body(d) : "", open);
  }

  function html() {
    if (!S.busy && Date.now() - S.at > STALE) load();
    if (S.err) return `<div class="err pad">Can't read the sync state: ${esc(S.err)}</div>`;
    if (!S.got) return '<div class="dim pad">Loading…</div>';
    const group = settingsForm.group, diff = S.got.diff || "";
    return `<div data-sync>${group("Claude sync", (S.got.directions || []).map(fold).join(""))}
      ${diff ? group("Instructions drift — ~/.claude/CLAUDE.md and AGENTS.md",
        `<pre style="white-space: pre-wrap; margin: 0; padding: 8px 12px; font-size: 12px">${esc(diff)}</pre>`) : ""}
      <div class="set-sub pad">The same in a terminal: <code>eki sync</code>, and <code>eki sync --diff</code>.</div></div>`;
  }

  function act(a, btn, card) {
    if (a !== "fold" || !card || !card.dataset.fold) return false;
    S.open = S.open === card.dataset.fold ? null : card.dataset.fold;
    return true;
  }

  const section = { title: "Claude sync", file: "routing", html, edit: () => false, act };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("sync", section);
  else (window.settingsSections = window.settingsSections || []).push(["sync", section]);
})();
