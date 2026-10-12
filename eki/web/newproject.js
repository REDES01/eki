// The "Create a project" sheet. It opens on three choices:
// - "Open a folder": in the Mac shell the native picker (window.ekiShell, shell.js) at once; in a browser a
//   path field with completion from GET /api/folders and the recent project folders as one-click rows. The
//   name defaults to the folder's last part.
// - "Start new": a name, and the folder it will make, from GET /api/projectstart?name= as you type
//   (~/Projects/<name>, or beside the project made last; never your home's root). "Change…" picks another
//   parent (the picker, or a path field); then the folder is <parent>/<name>, checked with ?folder=. With
//   the folder proposed and gh signed in, "Also create a GitHub repository" (Private checked) is offered.
// - "From GitHub": OWNER/REPO or a URL, the person's repositories as completion from GET /api/github when gh
//   is signed in, and where it clones (GET /api/github/clone-to) shown as you type, before Create.
// Each says how work lands: "eki merges here" or "eki opens pull requests on GitHub" — from GitHub by
// default; for a folder only when GET /api/github?folder= finds its GitHub remote (or a repository is made).
// Either way GET /api/projectstart answers {folder, base, recent, exists, project}: a folder that is a project
// already says "Already a project — open it" and Create opens that one instead of making a second. The goal
// ("What should this become?") is the first thing the project's agents read.
// Nothing is made until Create: POST /api/projects {name, description?, folder?, github?, lands, create_repo?,
// private?, history?} → {id, folder, lands, cloning, existing, warning?}, and the sheet goes to #project/<id>
// (also when existing). The API's error shows in the sheet; a warning (the project made, its repository not)
// shows with "Open the project".
// A folder eki keeps no history of yet comes back as {ask: {fit, offer, left_out, …}} with nothing made: the
// sheet keeps its fields and shows the offer in place of the error line, with "Keep a history" (history
// "start") and "Chat only" (history "none") — only "Chat only" when ask.fit is false — and what is kept out
// in a closed details fold. Outside that fold the sheet never says git, commit, branch or worktree.
(function () {
  let sheet = null, seq = 0, lookSeq = 0, timer = null;
  // mode: null (the three choices), "open", "new" or "github"; parent: a parent other than the proposed one;
  // named: the name was typed, so a chosen folder doesn't replace it; found: the project already on the folder
  const S = { mode: null, parent: "", named: false, found: null, recent: [] };
  // the GitHub part (newprojectgh.js): G = {gh, remote, made}, refresh, lookGh, ghSoon, lookDest
  let GH = null, G = {};
  const refresh = () => GH.refresh(), lookGh = (f) => GH.lookGh(f), ghSoon = () => GH.ghSoon(), lookDest = () => GH.lookDest();

  const canPick = () => !!(window.ekiShell && window.ekiShell.canPickFolder && window.ekiShell.canPickFolder());
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const last = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();
  const join = (parent, name) => String(parent).replace(/\/+$/, "") + "/" + name;

  const trim = (s) => String(s || "").trim();

  const EXAMPLE = "A small web game where you sort recycling against the clock, playable on a phone";

  function html(pick) {
    return `<form method="dialog" class="np-form" autocomplete="off">
      <h2>Create a project</h2>
      <div class="np-starts" id="np-starts">
        <button type="button" class="np-start" data-start="open"><b>Open a folder</b>
          <span>Work on something already on this Mac</span></button>
        <button type="button" class="np-start" data-start="new"><b>Start new</b>
          <span>A new folder for a new idea</span></button>
        <button type="button" class="np-start" data-start="github"><b>From GitHub</b>
          <span>Clone a repository and work on it</span></button>
      </div>
      <div class="np-body" id="np-body" hidden>
        <div id="np-open-at" hidden>
          <label for="np-folder">Folder</label>
          <div class="np-folder-row">
            <input id="np-folder" name="folder" list="np-folders" spellcheck="false" placeholder="/path/to/your/folder">
            ${pick ? '<button type="button" class="btn" id="np-choose">Choose…</button>' : ""}
          </div>
          <datalist id="np-folders"></datalist>
          <div class="np-recent" id="np-recent"></div>
        </div>
        <label for="np-name">Name</label>
        <input id="np-name" name="name" spellcheck="false" placeholder="A name">
        <div id="np-new-at" hidden>
          <div class="np-path">Makes <code id="np-path">…</code>
            <button type="button" class="np-link" id="np-change">Change…</button></div>
          <div class="np-parent-row" id="np-parent-row" hidden>
            <label for="np-parent">Inside this folder</label>
            <input id="np-parent" list="np-folders" spellcheck="false" placeholder="/path/to/a/folder">
          </div>
          <label class="np-pick" id="np-repo-box" hidden><input type="checkbox" id="np-create-repo"> Also create a GitHub repository</label>
          <label class="np-pick np-sub" id="np-private-box" hidden><input type="checkbox" id="np-private" checked> Private</label>
        </div>
        <div id="np-github-box" hidden>
          <label for="np-github">OWNER/REPO or a GitHub URL</label>
          <input id="np-github" name="github" list="np-repos" spellcheck="false" placeholder="owner/repo">
          <datalist id="np-repos"></datalist>
          <div class="np-note" id="np-gh-note"></div>
          <div class="np-note" id="np-dest"></div>
        </div>
        <div class="np-note" id="np-note" hidden></div>
        <label for="np-desc">What should this become?</label>
        <textarea id="np-desc" name="description" rows="3" placeholder="${esc(EXAMPLE)}"></textarea>
        <div class="np-hint">The first thing the project's agents read. A sentence or two is plenty.</div>
        <fieldset class="np-lands"><legend>How work lands</legend>
          <label class="np-pick"><input type="radio" name="lands" id="np-lands-here" value="here" checked> eki merges here</label>
          <label class="np-pick" id="np-pr-box"><input type="radio" name="lands" id="np-lands-github" value="github"> eki opens pull requests on GitHub</label>
          <div class="np-note" id="np-lands-why"></div>
        </fieldset>
      </div>
      <div class="error" id="np-err"></div>
      <div class="np-warn" id="np-warn" hidden></div>
      <div class="np-ask" id="np-ask" hidden></div>
      <div class="row-end">
        <button type="button" class="btn np-back" id="np-back" hidden>Back</button>
        <span class="grow"></span>
        <button type="button" class="btn" id="np-cancel">Cancel</button>
        <button type="submit" class="btn-primary" id="np-create" hidden>Create</button>
      </div></form>`;
  }

  // what Create posts: only the keys the mode has, and each optional one only when given
  function body(f, history) {
    f = f || {};
    const out = { name: trim(f.name) };
    if (trim(f.description)) out.description = trim(f.description);
    if (f.mode === "github") out.github = trim(f.github);
    else if (trim(f.folder)) out.folder = trim(f.folder);
    if (f.lands) out.lands = f.lands;
    if (f.mode !== "github" && !trim(f.folder) && f.create_repo) { out.create_repo = true; out.private = f.private !== false; }
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
    q("#np-create").hidden = !!survey || !S.mode;
    return html;
  }

  function choice(e) {
    const b = e && e.target && e.target.closest && e.target.closest("[data-history]");
    if (b) return create(null, b.dataset.history);
    return null;
  }

  const q = (sel) => sheet.querySelector(sel);

  // ---- where it goes ------------------------------------------------------------------------

  // the folder Create would use: the one opened, or (starting new) <parent>/<name> when a parent was chosen
  function target() {
    if (S.mode === "open") return q("#np-folder").value.trim();
    const name = q("#np-name").value.trim();
    return S.parent && name ? join(S.parent, name) : "";
  }

  // what the sheet says about the folder: already a project (Create opens it), or already there
  function note(got) {
    S.found = got && got.project ? got.project : null;
    const n = q("#np-note");
    let text = "";
    if (S.found) text = `<b>Already a project — open it.</b> Create opens it instead of making a second one.`;
    else if (got && S.mode === "new" && got.exists) text = `<code>${esc(got.folder)}</code> is already there: the project will use what's in it.`;
    else if (got && S.mode === "open" && got.exists === false) text = `There's no folder at <code>${esc(got.folder)}</code> yet: Create makes it.`;
    n.innerHTML = text;
    n.hidden = !text;
    n.className = "np-note" + (S.found ? " found" : got && got.exists && S.mode === "new" ? " warn" : "");
    q("#np-create").textContent = S.found ? "Open it" : "Create";
  }

  // GET /api/projectstart for what is typed now; a later call wins
  async function look() {
    const mine = ++lookSeq;
    const where = target();
    const name = q("#np-name").value.trim();
    let url;
    if (where) url = "/api/projectstart?folder=" + encodeURIComponent(where);
    else if (S.mode === "new" && name) url = "/api/projectstart?name=" + encodeURIComponent(name);
    if (!url) {
      if (S.mode === "new") q("#np-path").textContent = S.parent ? join(S.parent, "…") : "…";
      note(null);
      return null;
    }
    try {
      const r = await fetch(url);
      const got = await r.json().catch(() => ({}));
      if (mine !== lookSeq) return null;
      if (!r.ok) { if (S.mode === "new") q("#np-path").textContent = "…"; note(null); q("#np-err").textContent = got.error || ""; return null; }
      q("#np-err").textContent = "";
      if (S.mode === "new") q("#np-path").textContent = got.folder || "…";
      note(got);
      return got;
    } catch (e) { return null; }     // no answer this time; Create still asks the engine
  }

  function soon() { clearTimeout(timer); timer = setTimeout(look, 200); }

  // the recent project folders, one click each (the browser's open-a-folder)
  async function recent() {
    try {
      const r = await fetch("/api/projectstart?folder=" + encodeURIComponent("~"));
      const got = await r.json().catch(() => ({}));
      S.recent = got.recent || [];
    } catch (e) { S.recent = []; }
    q("#np-recent").innerHTML = S.recent.length ? `<div class="np-recent-h">Recent folders</div>` +
      S.recent.map((f, i) => `<button type="button" class="np-row" data-recent="${i}" title="${esc(f)}">
        <span class="np-row-name">${esc(last(f))}</span><span class="np-row-path">${esc(f)}</span></button>`).join("") : "";
    return S.recent;
  }

  async function complete(input) {
    const prefix = input.value;
    const mine = ++seq;
    if (!prefix.trim()) { q("#np-folders").innerHTML = ""; return; }
    try {
      const r = await fetch("/api/folders?prefix=" + encodeURIComponent(prefix));
      const got = await r.json();
      if (mine !== seq) return;
      q("#np-folders").innerHTML = (got.folders || []).map((f) => `<option value="${md.esc(f)}"></option>`).join("");
    } catch (e) { /* no completion this time; typing still works */ }
  }

  // a folder to open, from the picker, a recent row or the field: the name follows it unless typed
  function folder(path) {
    q("#np-folder").value = path;
    q("#np-folders").innerHTML = "";
    if (!S.named) q("#np-name").value = last(path);
    ask(null);
    ghSoon();
    return look();
  }

  // ---- the three starts ---------------------------------------------------------------------

  function show(mode) {
    S.mode = mode;
    q("#np-starts").hidden = !!mode;
    q("#np-body").hidden = !mode;
    q("#np-open-at").hidden = mode !== "open";
    q("#np-new-at").hidden = mode !== "new";
    q(mode === "github" ? "#np-lands-github" : "#np-lands-here").checked = true;
    if (mode !== "github") q("#np-lands-github").checked = false;
    G.remote = null;
    q("#np-back").hidden = !mode;
    q("#np-create").hidden = !mode;
    q("#np-err").textContent = "";
    ask(null);
    note(null);
    refresh();
  }

  async function start(mode) {
    if (mode === "open" && canPick()) {
      const path = await window.ekiShell.pickFolder();
      if (!path) return null;                 // cancelled: the two choices stay
      show("open");
      q("#np-recent").innerHTML = "";
      return folder(path);
    }
    show(mode);
    if (mode === "open") { q("#np-folder").focus(); return recent(); }
    if (mode === "github") { q("#np-github").focus(); return lookDest(); }
    q("#np-name").focus();
    return look();
  }

  // another parent for a new project: the picker, or the path field
  async function change() {
    if (canPick()) {
      const path = await window.ekiShell.pickFolder();
      if (!path) return null;
      S.parent = path;
      q("#np-parent").value = path;
      refresh();
      return look();
    }
    q("#np-parent-row").hidden = false;
    if (!q("#np-parent").value) q("#np-parent").value = S.parent || "";
    q("#np-parent").focus();
    return null;
  }

  async function choose() {
    const path = await window.ekiShell.pickFolder();
    return path ? folder(path) : null;
  }

  function opened(id) {
    sheet.close();
    nav.go("project/" + id);
  }

  function fields() {
    const lands = q("#np-lands-github").checked ? "github" : "here";
    return { mode: S.mode, name: q("#np-name").value, description: q("#np-desc").value, folder: target(),
             github: q("#np-github").value, lands, private: !!q("#np-private").checked,
             create_repo: !q("#np-repo-box").hidden && !!q("#np-create-repo").checked };
  }

  async function create(e, history) {
    if (e) e.preventDefault();
    if (G.made) { opened(G.made.id); return G.made; }
    if (S.found && !history) { opened(S.found); return { id: S.found, existing: true }; }
    const b = body(fields(), history);
    if (S.mode === "github" && !b.github) { q("#np-err").textContent = "Say which repository: OWNER/REPO or a URL."; return null; }
    if (S.mode === "open" && !b.folder) { q("#np-err").textContent = "Which folder?"; return null; }
    if (S.mode === "new" && !b.name) { q("#np-err").textContent = "Give it a name."; return null; }
    q("#np-err").textContent = "";
    q("#np-create").disabled = true;
    try {
      const r = await fetch("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(b) });
      const got = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(got.error || r.statusText || "Can't create the project");
      if (got.ask) { ask(got.ask); return got; }
      if (got.warning) {
        G.made = got;
        Object.assign(q("#np-warn"), { textContent: got.warning, hidden: false });
        q("#np-create").textContent = "Open the project";
        return got;
      }
      opened(got.id);
      return got;
    } catch (x) {
      q("#np-err").textContent = x.message;
      return null;
    } finally { q("#np-create").disabled = false; }
  }

  function onClick(e) {
    const t = e.target;
    const s = t && t.closest && t.closest("[data-start]");
    if (s) return start(s.dataset.start);
    const r = t && t.closest && t.closest("[data-recent]");
    if (r) return folder(S.recent[+r.dataset.recent]);
    return null;
  }

  function open() {
    if (!sheet) {
      sheet = document.createElement("dialog");
      sheet.className = "sheet";
      sheet.id = "new-project";
      document.body.appendChild(sheet);
    }
    Object.assign(S, { mode: null, parent: "", named: false, found: null, recent: [] });
    GH = window.newProjectGitHub({ q, S, target, esc, trim, live: () => !!sheet });
    G = GH.G;
    sheet.innerHTML = html(canPick());
    q("form").addEventListener("submit", create);
    q("form").addEventListener("click", onClick);
    q("#np-cancel").addEventListener("click", () => sheet.close());
    q("#np-back").addEventListener("click", () => show(null));
    q("#np-folder").addEventListener("input", () => {
      if (!S.named) q("#np-name").value = last(q("#np-folder").value);
      ask(null); complete(q("#np-folder")); soon(); ghSoon();
    });
    q("#np-name").addEventListener("input", () => { S.named = !!q("#np-name").value.trim(); ask(null); if (S.mode === "new") soon(); });
    q("#np-parent").addEventListener("input", () => { S.parent = q("#np-parent").value.trim(); complete(q("#np-parent")); soon(); refresh(); });
    q("#np-github").addEventListener("input", () => { q("#np-err").textContent = ""; return lookDest(); });
    q("#np-create-repo").addEventListener("change", () => {
      q(q("#np-create-repo").checked ? "#np-lands-github" : "#np-lands-here").checked = true;
      refresh();
    });
    q("#np-change").addEventListener("click", change);
    q("#np-ask").addEventListener("click", choice);
    const c = q("#np-choose");
    if (c) c.addEventListener("click", choose);
    sheet.showModal();
    lookGh("");
    return sheet;
  }

  window.newProject = { open, body, html, create, ask, start, look, change, folder, recent, refresh, lookGh, lookDest };
})();
