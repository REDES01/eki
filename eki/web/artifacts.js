// Artifacts: what your chats made — pictures, pages and documents — newest first under day
// headers, from /api/artifacts (eki/api_artifacts.py). It takes #main's place while the address
// is #artifacts; the old #pictures lands here showing Pictures (nav.js). A click shows the file
// in the side panel with what made it and a way back to its chat. Nothing is kept here that the
// API doesn't give back after a refresh.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const KINDS = [["all", "All"], ["picture", "Pictures"], ["page", "Pages"], ["document", "Documents"]];
  const HIDE = "eki.hideSensitive";   // the "Hide sensitive" toggle, kept in this window
  const kept = () => { try { return window.localStorage.getItem(HIDE) === "1"; } catch (e) { return false; } };
  const S = { on: false, kind: "all", q: "", items: [], more: false, loading: false, seq: 0, timer: null, hide: kept() };

  const ICON = { html: "🌐", htm: "🌐", md: "📝", markdown: "📝", pdf: "📄" };
  const icon = (ext) => ICON[String(ext || "").toLowerCase().replace(/^\./, "")] || "📎";

  function when(t) {
    return t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  }

  // [{label, items}] in the items' order: Today, Yesterday, then one header per date.
  function days(items, now) {
    const start = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
    const today = start(now == null ? Date.now() : now * 1000);
    const yesterday = start(today - 12 * 3600 * 1000);
    const out = [];
    for (const it of items) {
      const day = start((it.created_at || 0) * 1000);
      const d = new Date(day);
      const label = day === today ? "Today" : day === yesterday ? "Yesterday"
        : d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric",
          year: d.getFullYear() === new Date(today).getFullYear() ? undefined : "numeric" });
      if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] });
      out[out.length - 1].items.push(it);
    }
    return out;
  }

  // ---- the page -------------------------------------------------------------------------------

  const STYLE = `
    #artifacts-view { flex: 1; overflow-y: auto; }
    #artifacts-view[hidden] { display: none; }
    .af-day { font-size: 13px; color: var(--dim); margin: 22px 0 8px; font-weight: 500; }
    .af-pics { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
    .af-pics:empty, .af-docs:empty { display: none; }
    .af-docs { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; margin-top: 8px; }
    .af-doc { display: flex; gap: 10px; align-items: center; padding: 10px 12px; text-align: left; font: inherit; }
    .af-doc .af-icon { font-size: 20px; }
    .af-doc .af-text { min-width: 0; }
    .af-doc .af-name, .af-doc .quiet { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .af-tile { position: relative; }
    .af-tile .af-pmenu { position: absolute; top: 6px; right: 6px; z-index: 2; padding: 0 7px; line-height: 20px; border: 0;
      border-radius: 6px; background: rgba(0, 0, 0, .5); color: #fff; opacity: 0; cursor: pointer; }
    .af-tile:hover .af-pmenu, .af-pmenu:focus-visible { opacity: 1; }
    #af-hide.on { border-color: var(--teal); color: var(--teal); }
    .af-foot { text-align: center; margin: 20px 0; }
    .af-empty { text-align: center; margin-top: 20vh; }`;

  function box() {
    let el = $("artifacts-view");
    if (el) return el;
    if (!$("artifacts-style")) {
      const st = document.createElement("style");
      st.id = "artifacts-style";
      st.textContent = STYLE;
      document.head.appendChild(st);
    }
    el = document.createElement("div");
    el.id = "artifacts-view";
    el.innerHTML = `<div class="page">
      <div class="page-head"><h1 class="page-title">Artifacts</h1>
        <div class="actions">
          <div class="search" id="af-search"><button type="button" title="Search">⌕</button>
            <input id="af-q" placeholder="Search" spellcheck="false"></div>
          <div class="seg" id="af-seg">${KINDS.map(([k, t]) => `<button type="button" data-kind="${k}">${t}</button>`).join("")}</div>
          <button type="button" class="btn" id="af-hide" title="Leave out pictures marked sensitive">Hide sensitive</button>
        </div></div>
      <div id="af-list"></div>
      <div id="af-empty" class="quiet af-empty" hidden></div>
      <div class="af-foot"><button id="af-more" type="button" class="btn" hidden>More</button></div>
    </div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", onClick);
    $("af-q").addEventListener("input", () => {
      clearTimeout(S.timer);
      S.timer = setTimeout(() => search($("af-q").value), 250);
    });
    $("af-q").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { $("af-q").value = ""; search(""); $("af-search").classList.remove("open"); }
    });
    return el;
  }

  function onClick(e) {
    const k = e.target.closest("[data-kind]");
    if (k) return setKind(k.dataset.kind);
    if (e.target.closest("#af-more")) return more();
    if (e.target.closest("#af-hide")) return setHide(!S.hide);
    const pm = e.target.closest("[data-pmenu]");
    if (pm) return picMenu(+pm.dataset.pmenu, pm);
    if (e.target.closest("#af-search button")) {
      $("af-search").classList.add("open");
      return $("af-q").focus && $("af-q").focus();
    }
    const a = e.target.closest("[data-art]");
    if (a) open(+a.dataset.art);
  }

  function tile(p, i) {
    const url = "/api/file?path=" + encodeURIComponent(p.path);
    if (p.kind === "picture") {
      return `<div class="af-tile"><button type="button" class="gpic" data-art="${i}" title="${esc(p.prompt || p.name || p.path)}">
        <img src="${url}" alt="${esc(p.prompt || "")}" loading="lazy">
        ${p.row ? `<span class="gtag">${esc(p.row)}</span>` : ""}</button>
        <button type="button" class="af-pmenu" data-pmenu="${i}" title="Mark sensitive or not">⋯</button></div>`;
    }
    return `<button type="button" class="card af-doc" data-art="${i}" title="${esc(p.path)}">
      <span class="af-icon">${icon(p.ext)}</span>
      <span class="af-text"><span class="af-name">${esc(p.name || p.path.split("/").pop())}</span>
      <span class="quiet">${esc(p.thread_title || "")}</span></span></button>`;
  }

  function render() {
    const idx = new Map(S.items.map((it, i) => [it, i]));
    $("af-list").innerHTML = days(S.items).map((d) => `<section class="af-group">
      <h3 class="af-day">${esc(d.label)}</h3>
      <div class="af-pics">${d.items.filter((p) => p.kind === "picture").map((p) => tile(p, idx.get(p))).join("")}</div>
      <div class="af-docs">${d.items.filter((p) => p.kind !== "picture").map((p) => tile(p, idx.get(p))).join("")}</div>
    </section>`).join("");
    $("af-more").hidden = !S.more;
    const what = S.kind === "all" ? "artifacts" : KINDS.find(([k]) => k === S.kind)[1].toLowerCase();
    $("af-empty").textContent = S.q ? `No ${what} match “${S.q}”.` : S.hide ? `No ${what} to show: sensitive pictures are hidden.`
      : `No ${what} yet. What your chats make shows up here.`;
    $("af-empty").hidden = S.items.length > 0;
    document.querySelectorAll("#af-seg [data-kind]").forEach((b) => b.classList.toggle("on", b.dataset.kind === S.kind));
    $("af-hide").classList.toggle("on", S.hide);
    $("meta").textContent = `${S.items.length}${S.more ? "+" : ""} item${S.items.length === 1 ? "" : "s"}`;
  }

  async function load(fresh) {
    if (S.loading && !fresh) return;
    S.loading = true;
    const seq = ++S.seq;
    const before = !fresh && S.items.length ? S.items[S.items.length - 1].created_at : null;
    let url = "/api/artifacts?limit=60&kind=" + encodeURIComponent(S.kind);
    if (S.q) url += "&q=" + encodeURIComponent(S.q);
    if (S.hide) url += "&hide_sensitive=1";
    if (before != null) url += "&before=" + before;
    try {
      const r = await fetch(url);
      const got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
      if (!S.on || seq !== S.seq) return;
      S.items = fresh ? got.items || [] : S.items.concat(got.items || []);
      S.more = !!got.more;
      render();
    } catch (e) {
      if (seq === S.seq) $("meta").textContent = "Can't load artifacts: " + e.message;
    } finally {
      if (seq === S.seq) S.loading = false;
    }
  }

  function setKind(kind) {
    if (!KINDS.some(([k]) => k === kind)) kind = "all";
    S.kind = kind;
    S.items = [];
    document.querySelectorAll("#af-seg [data-kind]").forEach((b) => b.classList.toggle("on", b.dataset.kind === kind));
    return load(true);
  }

  function search(q) {
    S.q = String(q || "").trim();
    S.items = [];
    return load(true);
  }

  const more = () => load(false);

  function setHide(on) {
    S.hide = !!on;
    try { window.localStorage.setItem(HIDE, S.hide ? "1" : "0"); } catch (e) { /* no storage: this view only */ }
    S.items = [];
    return load(true);
  }

  // a picture tile's ⋯: the same mark / unmark as a right-click (sensitive.js); hidden ones leave
  function picMenu(i, btn) {
    const p = S.items[i];
    if (!p || !window.ekiSensitive) return;
    const r = btn.getBoundingClientRect();
    return window.ekiSensitive.menu(p.path, r.left, r.bottom + 2, (on) => {
      p.sensitive = on;
      if (S.hide && on) { S.items = []; load(true); }
    });
  }

  // the file in the side panel, then what made it and the way back to its chat
  async function open(i) {
    const p = S.items[i];
    if (!p) return;
    await panel.show(p.path);
    const d = document.createElement("div");
    d.className = "pinfo pad";
    const row = (k, v) => v ? `<div><span class="dim">${k}</span> ${v}</div>` : "";
    d.innerHTML = (p.prompt ? `<p class="pprompt">${esc(p.prompt)}</p>` : "") +
      row("kind", esc(p.row || p.kind || "")) + row("provider", esc(p.provider || "")) +
      row("run", p.run ? `<code>${esc(p.run)}</code>` : "") + row("made", esc(when(p.created_at))) +
      (p.thread ? `<div><a class="af-chat" href="#${encodeURIComponent(p.thread)}">Go to chat${p.thread_title ? ": " + esc(p.thread_title) : ""}</a></div>` : "");
    $("panel-body").appendChild(d);
  }

  function show(arg) {
    S.on = true;
    box().hidden = false;
    $("title").textContent = "Artifacts";
    $("meta").textContent = "";
    S.q = "";
    $("af-q").value = "";
    $("af-search").classList.remove("open");
    return setKind(arg || "all");
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    clearTimeout(S.timer);
    box().hidden = true;
  }

  window.artifacts = { show, hide, days, setKind, search, more, open, setHide, state: S, on: () => S.on };
  nav.register("artifacts", { show, hide });
})();
