// #projects: a card per project — name, two lines of description, last activity, and a quiet
// "~/folder · n chats" and a ⋯ menu to delete it (projectmenu.js) — with search (name or description, as you type), a sort switch (Recent
// activity / Name, remembered in localStorage) and "New project" (newproject.js). A card opens
// #project/<id> (project.js). Everything comes from GET /api/projects; reloads every 5 s while shown.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const EKI_DESC = "eki itself — its own source";
  const SORT_KEY = "eki.projects.sort";
  const S = { on: false, list: [], home: "", q: "", sort: remembered(), timer: null, last: "" };

  function remembered() {
    try { return localStorage.getItem(SORT_KEY) === "name" ? "name" : "recent"; } catch (e) { return "recent"; }
  }

  const describe = (p) => p.id === "eki" ? EKI_DESC : (p.description || "");
  const tilde = (f, home) => f && home && (f === home || f.startsWith(home + "/")) ? "~" + f.slice(home.length) : f;
  const day = (t) => t ? new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "";

  // The projects that match `q`, in `sort` order (eki stays where the order puts it).
  function pick(list, q, sort) {
    const needle = (q || "").trim().toLowerCase();
    const out = list.filter((p) => !needle || p.name.toLowerCase().includes(needle) ||
      describe(p).toLowerCase().includes(needle));
    return sort === "name"
      ? out.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }))
      : out.sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
  }

  function card(p, home) {
    const d = describe(p);
    const where = p.folder ? esc(tilde(p.folder, home)) : "no folder";
    return `<a class="card pp-card" href="#project/${encodeURIComponent(p.id)}" data-id="${esc(p.id)}">
      ${window.projectMenu ? window.projectMenu.button(p) : ""}<h3>${esc(p.name)}</h3>
      <div class="clamp pp-desc">${d ? esc(d) : '<span class="quiet">No description</span>'}</div>
      <div class="quiet pp-when">${p.last_active ? "Active " + esc(day(p.last_active)) : ""}</div>
      <div class="quiet pp-where">${where} · ${plural(p.chats || 0, "chat")}</div></a>`;
  }

  function cards(list, opts) {
    const o = opts || {};
    const ps = pick(list, o.q, o.sort);
    if (!ps.length) return `<div class="quiet">${list.length ? "No project matches." : "No projects yet."}</div>`;
    return ps.map((p) => card(p, o.home || "")).join("");
  }

  function render() {
    if (!S.on) return;
    const html = cards(S.list, { q: S.q, sort: S.sort, home: S.home });
    if (html !== S.last) { $("pp-cards").innerHTML = html; S.last = html; }
    for (const b of $("pp-sort").querySelectorAll("button")) b.classList.toggle("on", b.dataset.sort === S.sort);
  }

  function box() {
    let el = $("projects-page");
    if (el) return el;
    el = document.createElement("div");
    el.id = "projects-page";
    el.className = "page";
    el.innerHTML = `<div class="page-head">
        <h1 class="page-title">Projects</h1>
        <div class="actions">
          <div class="search" id="pp-search"><button type="button" title="Search">⌕</button>
            <input id="pp-q" placeholder="Search projects" spellcheck="false" autocomplete="off"></div>
          <div class="seg" id="pp-sort"><button type="button" data-sort="recent">Recent activity</button><button type="button" data-sort="name">Name</button></div>
          <button type="button" class="btn-primary" id="pp-new">New project</button>
        </div></div>
      <div class="cards" id="pp-cards"></div>`;
    $("main").insertBefore(el, $("composer"));
    $("pp-search").querySelector("button").addEventListener("click", () => {
      const open = $("pp-search").classList.toggle("open");
      if (open) $("pp-q").focus();
      else { $("pp-q").value = ""; S.q = ""; render(); }
    });
    $("pp-q").addEventListener("input", () => { S.q = $("pp-q").value; render(); });
    $("pp-q").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { $("pp-q").value = ""; S.q = ""; $("pp-search").classList.remove("open"); render(); }
    });
    $("pp-sort").addEventListener("click", (e) => {
      const b = e.target.closest("[data-sort]");
      if (!b) return;
      S.sort = b.dataset.sort;
      try { localStorage.setItem(SORT_KEY, S.sort); } catch (x) { /* private mode: just this time */ }
      render();
    });
    // the ⋯ on a card opens its menu, not the card's link; a delete reloads the list
    $("pp-cards").addEventListener("click", (e) => {
      const b = e.target.closest && e.target.closest("[data-pm]");
      if (!b) return;
      e.preventDefault(); e.stopPropagation();
      const p = S.list.find((x) => x.id === b.dataset.pm);
      if (p && window.projectMenu) window.projectMenu.open(p, b, () => load());
    });
    $("pp-new").addEventListener("click", () => window.newProject && window.newProject.open());
    return el;
  }

  async function load() {
    let got;
    try {
      const r = await fetch("/api/projects");
      got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
    } catch (e) { $("meta").textContent = "Can't load projects: " + e.message; return; }
    if (!S.on) return;
    S.list = got.projects || [];
    S.home = got.user_home || "";
    $("meta").textContent = plural(S.list.length, "project");
    render();
  }

  function show() {
    box().hidden = false;
    S.on = true; S.last = ""; S.sort = remembered();
    $("title").textContent = "Projects";
    $("meta").textContent = "";
    load();
    clearInterval(S.timer);
    S.timer = setInterval(load, 5000);
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    clearInterval(S.timer);
    box().hidden = true;
  }

  window.projectsPage = { show, hide, cards, pick, EKI_DESC };
  nav.register("projects", { show, hide });
})();
