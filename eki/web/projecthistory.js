// The project page's Changes section (#project/<id>, under Files): what landed in the project's folder,
// in plain words — "5 changes landed today", each change by eki or by you, and "Go back to before this".
// Branches, commits and worktrees stay inside each row's closed Details fold. project.js calls load()
// with each page load; everything comes from /api/projects/<id>/history and /goback (eki/api_history.py).
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const H = { id: null, changes: [], last: {}, err: "" };
  const when = (t) => (window.projects && window.projects.when ? window.projects.when(t) : "");

  // the section, made once inside the project page, the same pj-sect / fold as Chats and Files
  function section() {
    let s = $("pj-history");
    if (s) return s;
    const page = $("pj-page");
    if (!page) return null;
    s = document.createElement("section");
    s.className = "pj-sect";
    s.id = "pj-history";
    s.innerHTML = `<h3 class="pj-h" data-sect="history"><span class="pj-caret"></span>Changes
        <span class="pj-sum dim" id="pj-history-sum"></span></h3>
      <div class="pj-fold" id="pj-history-body"></div>`;
    page.appendChild(s);
    s.addEventListener("click", onClick);
    return s;
  }

  function put(id, html) {
    const el = $(id);
    if (el && H.last[id] !== html) { el.innerHTML = html; H.last[id] = html; }
  }

  // one change: its title, who made it, when, the button, and the closed Details fold
  function row(c, i) {
    return `<div class="pj-item"><span class="grow">${esc(c.title || "(untitled change)")}</span>
      <span class="dim">by ${c.by === "eki" ? "eki" : "you"}</span><span class="dim">${esc(when(c.when))}</span>
      <button type="button" class="pj-btn" data-goback="${i}">Go back to before this</button>
      <details class="pj-hist-details"><summary class="dim">Details</summary>
        <span class="dim">commit</span> <code>${esc(String(c.id || "").slice(0, 7))}</code></details></div>`;
  }

  // the section's summary line and rows for a history answer
  function html(h) {
    const changes = (h && h.changes) || [];
    return {
      sum: esc((h && h.line) || (changes.length ? "" : "no changes yet")),
      body: (changes.map(row).join("") || '<div class="dim">No changes yet.</div>') +
        (H.err ? `<div><span class="pj-err">${esc(H.err)}</span></div>` : ""),
    };
  }

  function render(h) {
    if (h) H.changes = h.changes || [];
    const got = html(h || { changes: H.changes, line: H.line });
    if (h) H.line = h.line;
    put("pj-history-sum", got.sum);
    put("pj-history-body", got.body);
  }

  async function load(id) {
    const s = section();
    if (!s) return;
    if (H.id !== id) { H.id = id; H.changes = []; H.line = ""; H.err = ""; H.last = {}; s.classList.remove("open"); }
    try {
      const r = await fetch("/api/projects/" + encodeURIComponent(id) + "/history");
      const got = await r.json().catch(() => ({}));
      if (H.id !== id) return;
      if (!r.ok || got.error) { put("pj-history-sum", "no history"); put("pj-history-body", `<div class="dim">${esc(got.error || r.statusText)}</div>`); return; }
      render(got);
    } catch (e) {
      if (H.id === id) put("pj-history-sum", "no history");   // the rest of the page still loads
    }
  }

  async function goBack(i, b) {
    const c = H.changes[i];
    if (!c || !H.id) return;
    if (!confirm(`Go back to before “${c.title}”? This change and every one after it are undone; going back can itself be undone.`)) return;
    const id = H.id;
    b.disabled = true;
    H.err = "";
    try {
      const r = await fetch("/api/projects/" + encodeURIComponent(id) + "/goback", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify({ change: c.id }) });
      const got = await r.json().catch(() => ({}));
      if (H.id !== id) return;
      if (!r.ok || got.error) throw new Error(got.error || r.statusText);
      render(got.history);
    } catch (e) {
      H.err = e.message;
      render(null);
    }
    b.disabled = false;
  }

  function onClick(e) {
    const b = e.target.closest("[data-goback]");
    if (b) { e.preventDefault(); goBack(+b.dataset.goback, b); }
  }

  window.projectHistory = { load, html };
})();
