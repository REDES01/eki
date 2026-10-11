// The page's addresses. #projects, #artifacts, #scheduled, #customize,
// #station and #settings are views that take #main's place, and
// #project/<id> is one project's page (#project/<id>/workflow its workflow);
// any other hash is a thread id, and none is a new thread. #settings/<section> is one section of Settings,
// #customize/<tab> one tab of Customize, and the old #pictures is Artifacts
// showing pictures. The address is the whole state, so a refresh reopens the
// same view.
(function () {
  const $ = (id) => document.getElementById(id);
  if (/EkiMac/.test(navigator.userAgent)) document.documentElement.dataset.shell = "mac";
  const RESERVED = ["station", "settings", "projects", "artifacts", "scheduled", "customize"];
  const views = {};
  let openThread = () => {};
  let started = false;

  function register(name, v) {
    views[name] = v;
    if (started && route(current())[0] === name) dispatch();
  }

  function current() {
    try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return location.hash.slice(1); }
  }

  // "project/<id>" is the project view with its id, "project/<id>/workflow" its Workflow page,
  // "settings/<section>" one section of Settings, "customize/<tab>" one tab of Customize; a reserved name is its own view.
  function route(name) {
    const w = /^project\/(.+)\/workflow$/.exec(name);
    if (w) return ["workflow", w[1]];
    const m = /^project\/(.+)$/.exec(name);
    if (m) return ["project", m[1]];
    const s = /^(settings|customize)\/(.+)$/.exec(name);
    if (s) return [s[1], s[2]];
    if (name === "pictures") return ["artifacts", "picture"];
    return [RESERVED.includes(name) ? name : "chat", null];
  }

  function dispatch() {
    const name = current();
    for (const v of Object.values(views)) v.hide();
    let [view, arg] = route(name);
    if (view === "artifacts" && !views.artifacts && views.pictures) view = "pictures";   // a build from before Artifacts
    $("app").dataset.view = view;
    // a project's pages light up Projects; the old Pictures link is Artifacts
    const link = view === "project" || view === "workflow" ? "projects" : view === "pictures" ? "artifacts" : view;
    const lit = (id) => (id === "pictures" ? "artifacts" : id) === link;
    document.querySelectorAll("#side .side-link").forEach((a) => a.classList.toggle("on", lit(a.id) && !(view === "chat" && name)));
    if (view === "chat") openThread(name || null);
    else if (views[view]) views[view].show(arg);
    else {  // a reserved view whose script isn't here: say so rather than show a broken chat
      $("title").textContent = view[0].toUpperCase() + view.slice(1);
      $("meta").textContent = "Not available in this build.";
    }
    window.dispatchEvent(new CustomEvent("eki:view", { detail: view }));
    requestAnimationFrame(drag);
  }

  function go(hash) {
    hash = (hash || "").replace(/^#/, "");
    if (current() === hash) dispatch();
    else location.hash = hash;
  }

  // The Mac window moves when dragged by its title areas; tell it where they are.
  function drag() {
    const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.eki;
    if (!h) return;
    const box = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
    h.postMessage({
      drag: [...document.querySelectorAll("[data-drag]")].map(box),
      nodrag: [...document.querySelectorAll("[data-drag] button, [data-drag] a, [data-drag] input, [data-drag] select")].map(box),
    });
  }

  function start() {
    started = true;
    window.addEventListener("hashchange", dispatch);
    new ResizeObserver(drag).observe(document.body);
    document.querySelectorAll("[data-drag]").forEach((el) => new ResizeObserver(drag).observe(el));
    window.addEventListener("resize", drag);
    dispatch();
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("#chat, #new");
    if (a) { e.preventDefault(); go(""); }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); go(""); }
  });

  window.nav = { register, thread: (fn) => { openThread = fn; }, go, drag, start, current, route };
})();
