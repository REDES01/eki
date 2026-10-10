// The page's addresses. #pictures, #station, #settings and #projects are views
// that take #main's place, and #project/<id> is one project's page; any other
// hash is a thread id, and none is a new thread. The address is the whole
// state, so a refresh reopens the same view.
(function () {
  const $ = (id) => document.getElementById(id);
  if (/EkiMac/.test(navigator.userAgent)) document.documentElement.dataset.shell = "mac";
  const RESERVED = ["pictures", "station", "settings", "projects"];
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

  // "project/<id>" is the project view with its id; a reserved name is its own view.
  function route(name) {
    const m = /^project\/(.+)$/.exec(name);
    if (m) return ["project", m[1]];
    return [RESERVED.includes(name) ? name : "chat", null];
  }

  function dispatch() {
    const name = current();
    for (const v of Object.values(views)) v.hide();
    const [view, arg] = route(name);
    $("app").dataset.view = view;
    const link = view === "project" ? "projects" : view;   // a project's page lights up Projects
    document.querySelectorAll("#side .side-link").forEach((a) => a.classList.toggle("on", a.id === link && !(view === "chat" && name)));
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

  window.nav = { register, thread: (fn) => { openThread = fn; }, go, drag, start, current };
})();
