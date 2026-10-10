// #project/<id>: one project's page — its description (Edit opens a textarea, Enter saves), a box
// for a new chat in its folder, then Chats, Background, Pictures and Files, each one summary line
// that opens on click; the header's ⋯ menu deletes it (projectmenu.js). The list of projects is projectspage.js. Everything comes from
// /api/projects (eki/api_projects.py); the page reloads every 5 s while shown and keeps nothing.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const S = { on: null, id: null, data: null, timer: null, open: new Set(), errs: {}, last: {}, home: "", editing: false };

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  // set a part only when its html changed, so what you are typing in it stays
  function put(id, html) {
    const el = $(id);
    if (el && S.last[id] !== html) { el.innerHTML = html; S.last[id] = html; }
  }
  const err = (key) => S.errs[key] ? `<span class="pj-err">${esc(S.errs[key])}</span>` : "";
  const tilde = (p) => p && S.home && (p === S.home || p.startsWith(S.home + "/")) ? "~" + p.slice(S.home.length) : p;

  function box() {
    let el = $("project-view");
    if (el) return el;
    el = document.createElement("div");
    el.id = "project-view";
    el.innerHTML = `<div class="pj-col">
      <div id="pj-page">
        <div id="pj-top" class="pj-top"></div>
        <div id="pj-desc" class="pj-desc"></div>
        <div id="pj-head" class="pj-headline"></div>
        <div id="pj-setup"></div>
        <div id="pj-cards"></div>
        <form id="pj-ask" class="pj-card" autocomplete="off">
          <textarea id="pj-ask-text" rows="2" placeholder="New chat here…"></textarea>
          <div class="pj-row"><span class="grow"></span><span id="pj-ask-err"></span>
            <button type="submit" class="pj-btn primary">New chat here</button></div>
        </form>
        ${["chats", "background", "pictures", "files"].map((k) => `<section class="pj-sect" id="pj-${k}">
          <h3 class="pj-h" data-sect="${k}"><span class="pj-caret"></span>${k[0].toUpperCase() + k.slice(1)}
            <span class="pj-sum dim" id="pj-${k}-sum"></span></h3>
          <div class="pj-fold" id="pj-${k}-body"></div></section>`).join("")}
      </div>
    </div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", onClick);
    el.addEventListener("keydown", onKey);
    $("pj-ask").addEventListener("submit", ask);
    return el;
  }

  // ---- #project/<id> --------------------------------------------------------------------------

  async function loadOne() {
    const id = S.id;
    let p;
    try { p = await api("/api/projects/" + encodeURIComponent(id)); } catch (e) {
      if (S.id === id) { $("title").textContent = "Project"; $("meta").textContent = e.message; }
      return;
    }
    if (S.on !== "project" || S.id !== id) return;
    S.data = p;
    S.home = p.user_home || S.home;
    $("title").textContent = p.name;
    $("meta").textContent = [p.kind, p.repo, p.instructions].filter(Boolean).join(" · ");
    put("pj-top", window.projectMenu ? window.projectMenu.button(p) : "");     // ⋯ → Delete project… (projectmenu.js)
    if (!S.editing) put("pj-desc", desc(p));
    put("pj-head", `<div>${p.folder ? `Chats work in <code>${esc(p.folder)}</code> — your real folder.` : "Chats need a folder — none yet."}</div>
      <div class="dim">Background: ${esc(p.background || "none")}</div>`);
    put("pj-setup", setup(p));
    $("pj-ask").hidden = !p.folder_ok;
    sections(p);
  }

  // The description under the title; eki's is fixed, so it has no Edit.
  function desc(p) {
    const text = p.id === "eki" ? "eki itself — its own source" : (p.description || "");
    const edit = p.id === "eki" ? "" : ` <a href="#" class="pj-edit" data-act="describe">${text ? "Edit" : "Add a description"}</a>`;
    return `<p class="pj-desc-text">${text ? esc(text) : ""}${edit}</p>${err("describe")}`;
  }

  function editDesc() {
    S.editing = true;
    delete S.errs.describe;
    $("pj-desc").innerHTML = `<textarea id="pj-desc-edit" rows="3" placeholder="What are you trying to achieve?"></textarea>
      <div class="quiet">Enter saves · Esc cancels</div>`;
    S.last["pj-desc"] = null;
    const t = $("pj-desc-edit");
    t.value = (S.data && S.data.description) || "";
    t.focus();
  }

  async function saveDesc(text) {
    try {
      await api("/api/projects/" + encodeURIComponent(S.id) + "/describe", { description: text });
      if (S.data) S.data.description = text.trim() || null;
    } catch (x) { S.errs.describe = x.message; }
    S.editing = false;
    put("pj-desc", desc(S.data || { id: S.id }));
  }

  function onKey(e) {
    if (e.target.id !== "pj-desc-edit") return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveDesc(e.target.value); }
    else if (e.key === "Escape") { e.preventDefault(); S.editing = false; put("pj-desc", desc(S.data || { id: S.id })); }
  }

  // What the project still needs: a folder (pick one, or clone it home), or instructions.
  function setup(p) {
    if (p.cloning) return `<div class="pj-card dim">cloning ${esc(p.repo)} to <code>${esc(p.folder)}</code>…</div>`;
    if (!p.folder || !p.folder_ok) {
      const name = p.repo ? p.repo.split("/").pop() : "";
      return `<div class="pj-card">
        <div class="pj-sub">${p.folder ? `<code>${esc(p.folder)}</code> is missing.` : "No folder yet."} Chats need one.</div>
        <form id="pj-folder" class="pj-row" autocomplete="off"><input id="pj-folder-path" class="grow" placeholder="/path/to/your/folder" spellcheck="false">
          <button type="submit" class="pj-btn">Pick this folder</button></form>
        ${p.repo && !p.folder ? `<div class="pj-row"><button type="button" class="pj-btn" data-act="clone">Clone to ~/${esc(name)}</button></div>` : ""}
        ${err("folder")}</div>`;
    }
    if (!p.instructions) return `<div class="pj-card pj-row"><span class="dim grow">No AGENTS.md or CLAUDE.md in this folder.</span>
        <button type="button" class="pj-btn" data-act="instructions">Have an agent write instructions</button>${err("instructions")}</div>`;
    return "";
  }

  function when(t) {
    return t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  }

  function sections(p) {
    const s = p.sections || {};
    const chats = s.chats || [], bg = s.background || { goals: [], standing: [], prs: [] };
    const pics = (s.pictures || {}).items || [], files = s.files || [];
    put("pj-chats-sum", plural(chats.length, "chat"));
    put("pj-chats-body", chats.map((t) => `<a class="pj-item" href="#${encodeURIComponent(t.id)}">
        ${t.working ? '<span class="pj-dot" title="working"></span>' : ""}<span class="grow">${esc(t.title || "(untitled)")}</span>
        <span class="dim">${when(t.updated)}</span></a>`).join("") || '<div class="dim">No chats yet.</div>');
    put("pj-background-sum", `${plural(p.goals, "goal")} · ${p.prs} PR${p.prs === 1 ? "" : "s"} open`);
    const goals = [...(bg.standing || []).map((g) => ({ ...g, standing: true })), ...(bg.goals || [])];
    put("pj-background-body", (goals.map((g) => `<div class="pj-item"><span class="pj-state">${esc(g.state)}</span>
        <span class="grow">${esc(g.text)}</span>${g.standing ? '<span class="dim">standing</span>' : ""}</div>`).join("") ||
        '<div class="dim">No background work.</div>') +
      (bg.prs || []).map((r) => `<div class="pj-item">${prs.link(r.url)} <span class="grow">${esc(r.title)}</span></div>`).join(""));
    put("pj-pictures-sum", plural(pics.length, "picture"));
    put("pj-pictures-body", pics.length ? `<div class="pj-grid">${pics.map((x, i) => `<button type="button" class="gpic" data-pic="${i}" title="${esc(x.prompt || x.path)}">
        <img src="/api/file?path=${encodeURIComponent(x.path)}" alt="${esc(x.prompt || "")}" loading="lazy">
        <span class="gtag">${esc(x.row || "image")}</span></button>`).join("")}</div>` : '<div class="dim">No pictures yet.</div>');
    put("pj-files-sum", plural(files.length, "file"));
    const rel = (f) => p.folder && f.startsWith(p.folder + "/") ? f.slice(p.folder.length + 1) : tilde(f);
    put("pj-files-body", files.length ? `<div class="files">${files.map((f) =>
        `<button type="button" class="file" data-file="${esc(f)}" title="${esc(f)}">${esc(rel(f))}</button>`).join("")}</div>`
      : '<div class="dim">No files yet.</div>');
    for (const k of ["chats", "background", "pictures", "files"]) $(`pj-${k}`).classList.toggle("open", S.open.has(k));
  }

  // a picture opens in the panel with what made it, as in the gallery
  async function picture(x) {
    if (!x) return;
    await panel.show(x.path);
    const d = document.createElement("div");
    d.className = "pinfo pad";
    const row = (k, v) => v ? `<div><span class="dim">${k}</span> ${v}</div>` : "";
    d.innerHTML = (x.prompt ? `<p class="pprompt">${esc(x.prompt)}</p>` : "") +
      row("kind", esc(x.row || "")) + row("provider", esc(x.provider || "")) + row("made", esc(when(x.created_at))) +
      (x.thread ? `<div><a href="#${encodeURIComponent(x.thread)}">Open thread${x.thread_title ? ": " + esc(x.thread_title) : ""}</a></div>` : "");
    $("panel-body").appendChild(d);
  }

  async function act(key, fn) {
    delete S.errs[key];
    try { return await fn(); } catch (x) { S.errs[key] = x.message; put("pj-setup", setup(S.data)); return null; }
  }

  async function onClick(e) {
    const h = e.target.closest("[data-sect]");
    if (h) {
      const k = h.dataset.sect;
      S.open.has(k) ? S.open.delete(k) : S.open.add(k);
      $(`pj-${k}`).classList.toggle("open", S.open.has(k));
      return;
    }
    const more = e.target.closest("[data-pm]");
    if (more && S.data) {
      e.preventDefault(); e.stopPropagation();
      return window.projectMenu && window.projectMenu.open(S.data, more, () => nav.go("#projects"));
    }
    const pic = e.target.closest("[data-pic]");
    if (pic) return picture((((S.data || {}).sections || {}).pictures || {}).items[+pic.dataset.pic]);
    const b = e.target.closest("[data-act]");
    if (!b || !S.id) return;
    if (b.dataset.act === "describe") { e.preventDefault(); return editDesc(); }
    b.disabled = true;
    const base = "/api/projects/" + encodeURIComponent(S.id);
    if (b.dataset.act === "clone") {
      await act("folder", () => api(base + "/clone", {}));
      loadOne();
    } else if (b.dataset.act === "instructions") {
      const got = await act("instructions", () => api(base + "/instructions", {}));
      if (got) nav.go(got.thread);
    }
    b.disabled = false;
  }

  document.addEventListener("submit", async (e) => {
    if (e.target.id !== "pj-folder") return;
    e.preventDefault();
    const folder = $("pj-folder-path").value.trim();
    if (!folder) return;
    const ok = await act("folder", () => api("/api/projects/" + encodeURIComponent(S.id) + "/folder", { folder }));
    if (ok) loadOne();
  });

  async function ask(e) {
    e.preventDefault();
    const prompt = $("pj-ask-text").value.trim();
    if (!prompt || !S.id) return;
    try {
      const got = await api("/api/ask", { prompt, project: S.id });
      $("pj-ask-text").value = "";
      put("pj-ask-err", "");
      nav.go(got.thread);
    } catch (x) { put("pj-ask-err", `<span class="pj-err">${esc(x.message)}</span>`); }
  }

  // ---- the view -------------------------------------------------------------------------------

  function start(id) {
    const el = box();
    el.hidden = false;
    if (S.id !== id) { S.open.clear(); S.errs = {}; S.data = null; }
    S.on = "project"; S.id = id; S.last = {}; S.editing = false;
    $("title").textContent = "Project";
    $("meta").textContent = "";
    loadOne();
    clearInterval(S.timer);
    S.timer = setInterval(loadOne, 5000);
    window.projectCards && projectCards.mount(document.getElementById("pj-cards"), id);
  }

  function hide() {
    if (!S.on) return;
    S.on = null;
    clearInterval(S.timer);
    window.projectCards && projectCards.unmount();
    box().hidden = true;
  }

  // Until index.html names the Projects page's scripts (the sidebar item), fetch them from here.
  for (const f of ["shell.js", "newproject.js", "projectmenu.js", "projectspage.js"]) {
    if (!document.querySelector || document.querySelector(`script[src="/ui/${f}"]`)) continue;
    const t = document.createElement("script");
    t.src = "/ui/" + f; t.async = false;
    document.head.appendChild(t);
  }
  if (document.querySelector && !document.querySelector('link[href="/ui/pages.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/pages.css";
    document.head.appendChild(l);
  }

  window.projects = { show: start, hide };
  nav.register("project", { show: start, hide });
})();
