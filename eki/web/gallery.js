// The gallery: every picture eki drew, newest first, from /api/pictures.
// It takes #main's place while the address is #pictures; nothing is kept here
// that the API doesn't give back after a refresh.
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { on: false, items: [], more: false, loading: false };

  function when(t) {
    return t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  }

  function box() {
    let el = $("gallery");
    if (!el) {
      el = document.createElement("div");
      el.id = "gallery";
      el.innerHTML = '<div id="grid"></div><div class="gmore"><button id="gallery-more" type="button" class="ghost" hidden>More</button></div>' +
        '<div id="gallery-empty" class="dim" hidden>No pictures yet. Ask eki to draw something.</div>';
      $("main").insertBefore(el, $("composer"));
      $("gallery-more").addEventListener("click", () => load(false));
      $("grid").addEventListener("click", (e) => {
        const b = e.target.closest("[data-pic]");
        if (b) detail(state.items[+b.dataset.pic]);
      });
    }
    return el;
  }

  function tile(p, i) {
    const url = "/api/file?path=" + encodeURIComponent(p.path);
    return `<button type="button" class="gpic" data-pic="${i}" title="${md.esc(p.prompt || p.path)}">
      <img src="${url}" alt="${md.esc(p.prompt || "")}" loading="lazy">
      <span class="gtag">${md.esc(p.row || "image")}</span></button>`;
  }

  async function load(fresh) {
    if (state.loading) return;
    state.loading = true;
    const before = !fresh && state.items.length ? state.items[state.items.length - 1].created_at : null;
    try {
      const r = await fetch("/api/pictures?limit=60" + (before != null ? "&before=" + before : ""));
      const got = await r.json();
      if (!r.ok) throw new Error(got.error || r.statusText);
      if (!state.on) return;
      state.items = fresh ? got.pictures : state.items.concat(got.pictures);
      state.more = !!got.more;
      $("grid").innerHTML = state.items.map(tile).join("");
      $("gallery-more").hidden = !state.more;
      $("gallery-empty").hidden = state.items.length > 0;
      $("meta").textContent = `${state.items.length}${state.more ? "+" : ""} picture${state.items.length === 1 ? "" : "s"}`;
    } catch (e) {
      $("meta").textContent = "Can't load pictures: " + e.message;
    } finally {
      state.loading = false;
    }
  }

  async function detail(p) {
    if (!p) return;
    await panel.show(p.path);
    const d = document.createElement("div");
    d.className = "pinfo pad";
    const row = (k, v) => v ? `<div><span class="dim">${k}</span> ${v}</div>` : "";
    d.innerHTML = (p.prompt ? `<p class="pprompt">${md.esc(p.prompt)}</p>` : "") +
      row("kind", md.esc(p.row || "")) + row("provider", md.esc(p.provider || "")) +
      row("run", `<code>${md.esc(p.run || "")}</code>`) + row("made", md.esc(when(p.created_at))) +
      (p.thread ? `<div><a href="#${encodeURIComponent(p.thread)}">Open thread${p.thread_title ? ": " + md.esc(p.thread_title) : ""}</a></div>` : "");
    $("panel-body").appendChild(d);
  }

  function show() {
    state.on = true;
    box().hidden = false;
    $("app").classList.add("gallery");
    $("pictures").classList.add("on");
    $("title").textContent = "Pictures";
    $("meta").textContent = "";
    state.items = [];
    load(true);
  }

  function hide() {
    if (!state.on) return;
    state.on = false;
    box().hidden = true;
    $("app").classList.remove("gallery");
    $("pictures").classList.remove("on");
  }

  window.gallery = { show, hide, on: () => state.on };
})();
