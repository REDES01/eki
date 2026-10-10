// The sidebar: the chat list (sidelist.js draws it) and what a click in it does, from the API. The links above it
// are index.html's; the status row and its popover below it are sidestatus.js.
(function () {
  const $ = (id) => document.getElementById(id);

  async function call(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  // ---- threads: drawn by sidelist.js --------------------------------------------------------

  let lastList = [], lastGroups = [], lastArchived = [];
  function render(list, cur, groups, archived) { return sidelist.render(list, cur, groups, archived); }
  function draw() { $("threads").innerHTML = render(lastList, nav.current(), lastGroups, lastArchived); }

  async function loadThreads() {
    const [list, groups, archived] = await Promise.all([
      call("/api/threads"), call("/api/groups").catch(() => []), call("/api/threads?archived=1").catch(() => []),
    ]);
    lastList = list; lastGroups = groups;
    lastArchived = archived.filter((t) => t.archived);   // an engine without ?archived=1 sends the whole list
    draw();
  }

  document.addEventListener("click", async (e) => {
    if (e.target.closest(".tmore")) { e.preventDefault(); return; }   // the … opens the menu (threadmenu.js), not the chat
    const pg = e.target.closest("[data-pgroup]");
    if (pg) {   // a project header opens to its chats, or closes
      sidelist.opened.set(pg.dataset.pgroup, !pg.closest(".pgroup").classList.contains("open"));
      return draw();
    }
    const cg = e.target.closest("[data-cgroup]");
    if (cg) {   // one of your groups, the same
      sidelist.gopened.set(cg.dataset.cgroup, !cg.closest(".cgroup").classList.contains("open"));
      return draw();
    }
    if (e.target.closest("[data-archived]")) { sidelist.state.archived = !sidelist.state.archived; return draw(); }
    const un = e.target.closest("[data-unarchive]");
    if (un) {
      un.disabled = true;
      await call(`/api/threads/${un.dataset.unarchive}`, { archive: false }).catch((x) => { $("engine").textContent = x.message; });
      return loadThreads().catch(() => {});
    }
  });
  window.addEventListener("eki:view", () => loadThreads().catch(() => {}));

  setInterval(() => loadThreads().catch(() => {}), 4000);
  window.side = { loadThreads, render, list: () => lastList.concat(lastArchived) };   // list: threadmenu.js
})();
