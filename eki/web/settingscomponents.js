// Settings → Components, read-only: the machine-wide pool, then each project's own components
// (GET /api/components, eki/api_components.py). One folded line per component — name, kind,
// about; a broken one in red with its error — that opens to its folder, component.json and
// notes, one open at a time. It edits no file: `eki component add` writes component folders.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { got: null, err: null, busy: false, at: 0, open: null };
  const STALE = 10000;   // read again when the section is drawn and this is older (ms)

  async function load() {
    S.busy = true;
    try {
      const r = await fetch("/api/components");
      const got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
      [S.got, S.err] = [got, null];
    } catch (e) {
      S.err = e.message;
    } finally {
      [S.busy, S.at] = [false, Date.now()];
    }
    if (settings.on() && location.hash.startsWith("#settings/components")) settings.render();
  }

  function body(c) {
    const line = settingsForm.line, block = settingsForm.block;
    return settingsForm.lines("",
      line("Folder", `<code>${esc(c.folder)}</code>`, esc(c.scope)) +
      (c.error ? line('<span class="set-red-label">Broken</span>', `<span class="set-red-label">${esc(c.error)}</span>`) : "") +
      block("component.json", c.spec ? `<pre style="white-space: pre-wrap; margin: 0; font-size: 12px">${esc(JSON.stringify(c.spec, null, 2))}</pre>` : '<span class="dim">unreadable</span>') +
      (c.notes && c.notes.length ? block("Notes", c.notes.map((n) => `<div>${esc(n)}</div>`).join("")) : ""));
  }

  function fold(c) {
    const sum = c.error
      ? `<b class="set-red-label">${esc(c.name)}</b> <span class="set-red-label">broken — ${esc(c.error)}</span>`
      : `<b>${esc(c.name)}</b> <span class="dim">${esc(c.kind)}</span> <span class="dim">${esc(c.about)}</span>`;
    const id = c.folder, open = S.open === id;
    return settingsForm.fold(id, sum, open ? body(c) : "", open);
  }

  const list = (rows, none) => rows.length ? rows.map(fold).join("") : `<div class="set-line dim">${esc(none)}</div>`;

  function html() {
    if (!S.busy && Date.now() - S.at > STALE) load();
    if (S.err) return `<div class="err pad">Can't read the components: ${esc(S.err)}</div>`;
    if (!S.got) return '<div class="dim pad">Loading…</div>';
    const group = settingsForm.group;
    return `<div data-components>${group("Pool — every project", list(S.got.pool || [], "none in ~/.eki/components"))}
      ${(S.got.projects || []).map((p) => group(`${esc(p.project)} <span class="dim">${esc(p.folder)}</span>`,
        list(p.components || [], "no components/ folder"))).join("")}
      <div class="set-sub pad">Add one with <code>eki component add &lt;kind|template&gt;</code>; <code>eki component kinds</code> lists them.</div></div>`;
  }

  function act(a, btn, card) {
    if (a !== "fold" || !card || !card.dataset.fold) return false;
    S.open = S.open === card.dataset.fold ? null : card.dataset.fold;
    return true;
  }

  const section = { title: "Components", file: "routing", html, edit: () => false, act };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("components", section);
  else (window.settingsSections = window.settingsSections || []).push(["components", section]);
})();
