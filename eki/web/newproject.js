// The "Create a project" sheet: a name (required), what you are trying to achieve (optional), and
// an optional folder with completion from GET /api/folders and, in the Mac shell, "Choose…"
// (window.ekiShell, shell.js). Without a folder the API makes ~/<name>. Create posts
// POST /api/projects {name, folder?, description?, history?}; the API's error shows in the sheet, and
// a new project opens at #project/<id>. A folder eki keeps no history of yet comes back as
// {ask: {fit, offer, left_out, …}} with nothing made: the sheet keeps its fields and shows the offer in
// place of the error line, with "Keep a history" (history "start") and "Chat only" (history "none")
// — only "Chat only" when ask.fit is false — and what is kept out in a closed details fold. Outside
// that fold the sheet never says git, commit, branch or worktree.
(function () {
  let sheet = null, seq = 0;

  const canPick = () => !!(window.ekiShell && window.ekiShell.canPickFolder && window.ekiShell.canPickFolder());

  function html(pick) {
    return `<form method="dialog" class="np-form" autocomplete="off">
      <h2>Create a project</h2>
      <label for="np-name">What are you working on?</label>
      <input id="np-name" name="name" required spellcheck="false" placeholder="A name">
      <label for="np-desc">What are you trying to achieve?</label>
      <textarea id="np-desc" name="description" rows="3" placeholder="Optional"></textarea>
      <label for="np-folder">Use a folder</label>
      <div class="np-folder-row" style="display:flex;gap:8px">
        <input id="np-folder" name="folder" list="np-folders" spellcheck="false" placeholder="Optional — eki makes ~/name">
        ${pick ? '<button type="button" class="btn" id="np-choose">Choose…</button>' : ""}
      </div>
      <datalist id="np-folders"></datalist>
      <div class="error" id="np-err"></div>
      <div class="np-ask" id="np-ask" hidden></div>
      <div class="row-end">
        <button type="button" class="btn" id="np-cancel">Cancel</button>
        <button type="submit" class="btn-primary" id="np-create">Create project</button>
      </div></form>`;
  }

  // what Create posts: the name, and the folder, description and history only when given
  function body(name, folder, description, history) {
    const out = { name: String(name || "").trim() };
    if (String(folder || "").trim()) out.folder = String(folder).trim();
    if (String(description || "").trim()) out.description = String(description).trim();
    if (history) out.history = history;
    return out;
  }

  // the offer for a folder with no history yet: its words, what stays out (folded), and the choices
  function offer(survey) {
    const s = survey || {};
    const out = (s.left_out || []).map((p) => `<li>${md.esc(p)}</li>`).join("");
    const fold = out ? `<details class="np-left-out"><summary>Kept out of the history (offered as the project's media pool)</summary>
        <ul>${out}</ul></details>` : "";
    const keep = s.fit === false ? "" : '<button type="button" class="btn-primary" data-history="start">Keep a history</button>';
    return `<p class="np-offer">${md.esc(s.offer || "")}</p>${fold}
      <div class="row-end">${keep}<button type="button" class="btn" data-history="none">Chat only</button></div>`;
  }

  // shows the offer in place of the error line (and the Create button); ask(null) takes it away
  function ask(survey) {
    const html = survey ? offer(survey) : "";
    if (!sheet) return html;
    const box = q("#np-ask");
    box.innerHTML = html;
    box.hidden = !survey;
    q("#np-err").textContent = "";
    q("#np-create").hidden = !!survey;
    return html;
  }

  function choice(e) {
    const b = e && e.target && e.target.closest && e.target.closest("[data-history]");
    if (b) return create(null, b.dataset.history);
    return null;
  }

  const q = (sel) => sheet.querySelector(sel);

  async function complete() {
    const prefix = q("#np-folder").value;
    const mine = ++seq;
    if (!prefix.trim()) { q("#np-folders").innerHTML = ""; return; }
    try {
      const r = await fetch("/api/folders?prefix=" + encodeURIComponent(prefix));
      const got = await r.json();
      if (mine !== seq) return;
      q("#np-folders").innerHTML = (got.folders || []).map((f) => `<option value="${md.esc(f)}"></option>`).join("");
    } catch (e) { /* no completion this time; typing still works */ }
  }

  async function choose() {
    const path = await window.ekiShell.pickFolder();
    if (path) { q("#np-folder").value = path; q("#np-folders").innerHTML = ""; }
  }

  async function create(e, history) {
    if (e) e.preventDefault();
    const b = body(q("#np-name").value, q("#np-folder").value, q("#np-desc").value, history);
    if (!b.name) { q("#np-err").textContent = "Give it a name."; return null; }
    q("#np-err").textContent = "";
    q("#np-create").disabled = true;
    try {
      const r = await fetch("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(b) });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(got.error || r.statusText || "Can't create the project");
      if (got.ask) { ask(got.ask); return got; }
      sheet.close();
      nav.go("project/" + got.id);
      return got;
    } catch (x) {
      q("#np-err").textContent = x.message;
      return null;
    } finally { q("#np-create").disabled = false; }
  }

  function open() {
    if (!sheet) {
      sheet = document.createElement("dialog");
      sheet.className = "sheet";
      sheet.id = "new-project";
      document.body.appendChild(sheet);
    }
    sheet.innerHTML = html(canPick());
    q("form").addEventListener("submit", create);
    q("#np-cancel").addEventListener("click", () => sheet.close());
    q("#np-folder").addEventListener("input", () => { ask(null); complete(); });
    q("#np-ask").addEventListener("click", choice);
    const c = q("#np-choose");
    if (c) c.addEventListener("click", choose);
    sheet.showModal();
    q("#np-name").focus();
    return sheet;
  }

  window.newProject = { open, body, html, create, ask };
})();
