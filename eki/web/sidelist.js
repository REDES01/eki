// The sidebar's chat list as html: eki's threads that need you, Pinned, your groups, then chats in a project under its
// header and the rest by when they last moved; an Archived line at the bottom. side.js loads the list and draws it.
(function () {
  function ago(t) {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    return Math.floor(s / 86400) + "d";
  }

  function group(t) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = today.getTime() / 1000;
    if (t >= day) return "Today";
    if (t >= day - 86400) return "Yesterday";
    if (t >= day - 7 * 86400) return "Previous 7 days";
    return "Older";
  }

  // needs you, then working, then unread: one dot, the most pressing
  function dot(t) {
    if (t.needs_you) return '<span class="dot you" title="needs you"></span>';
    if (t.working) return '<span class="dot" title="working"></span>';
    if (t.unread) return '<span class="dot unread" title="unread"></span>';
    return "";
  }

  // a small mark on a chat with unsent text or pictures (ekiDrafts, attach.js)
  const draft = (t) => window.ekiDrafts && ekiDrafts.has(t.id) ? '<span class="draft" title="Unsent draft">draft</span> ' : "";

  function threadLink(t, cur) {
    return `
      <a href="#${t.id}" class="thread ${t.id === cur ? "on" : ""}" data-id="${t.id}">
        ${dot(t)}
        <span class="t">${md.esc(t.title || "(untitled)")}</span>
        <span class="when dim">${draft(t)}${t.provider ? md.esc(t.provider) + " · " : ""}${ago(t.updated)}</span>
        <button type="button" class="tmore" data-tmore="${t.id}" title="More" aria-label="More">…</button>
      </a>`;
  }

  // Project groups and your groups you opened or closed: what the page shows, not keeps.
  // Unset, a project is open when it holds the current thread; a group of yours is open.
  const opened = new Map(), gopened = new Map();
  const state = { archived: false };   // the Archived list shown inline

  function projects(list, cur) {
    const byProject = new Map();
    for (const t of list) {
      if (!byProject.has(t.project.id)) byProject.set(t.project.id, { p: t.project, chats: [] });
      byProject.get(t.project.id).chats.push(t);
    }
    const groups = [...byProject.values()];
    groups.forEach((g) => g.chats.sort((a, b) => b.updated - a.updated));
    groups.sort((a, b) => b.chats[0].updated - a.chats[0].updated);
    let html = "";
    for (const { p, chats } of groups) {
      const open = opened.has(p.id) ? opened.get(p.id) : chats.some((t) => t.id === cur);
      const busy = chats.some((t) => t.needs_you) ? '<span class="dot you" title="needs you"></span>'
        : chats.some((t) => t.working) ? '<span class="dot" title="working"></span>' : "";
      html += `<div class="pgroup ${open ? "open" : ""}">
        <div class="phead"><button type="button" class="ptoggle" data-pgroup="${md.esc(p.id)}" aria-expanded="${open}"
            title="Show its chats">${open ? "▾" : "▸"}</button>
          <a href="#project/${encodeURIComponent(p.id)}" class="pname">${md.esc(p.name || p.id)}</a>${busy}
          <button type="button" class="pcount dim" data-pgroup="${md.esc(p.id)}">${chats.length} chat${chats.length === 1 ? "" : "s"}</button></div>
        ${open ? `<div class="pchats">${chats.map((t) => threadLink(t, cur)).join("")}</div>` : ""}</div>`;
    }
    return html;
  }

  // Your groups in their order, each a section that opens and closes; a group with no chat isn't shown.
  function chatGroups(list, cur, groups) {
    const by = new Map();
    for (const t of list) {
      if (!by.has(t.group.id)) by.set(t.group.id, []);
      by.get(t.group.id).push(t);
    }
    const order = (groups || []).filter((g) => by.has(g.id));
    for (const t of list) if (!order.some((g) => g.id === t.group.id)) order.push(t.group);   // one the list knows first
    let html = "";
    for (const g of order) {
      const chats = by.get(g.id).sort((a, b) => b.updated - a.updated);
      const open = gopened.has(g.id) ? gopened.get(g.id) : true;
      html += `<div class="cgroup ${open ? "open" : ""}">
        <button type="button" class="chead" data-cgroup="${md.esc(g.id)}" aria-expanded="${open}">
          <span class="ptoggle">${open ? "▾" : "▸"}</span><span class="cname">${md.esc(g.name || g.id)}</span>
          <span class="dim">${chats.length}</span></button>
        ${open ? `<div class="pchats">${chats.map((t) => threadLink(t, cur)).join("")}</div>` : ""}</div>`;
    }
    return html;
  }

  function archivedLine(archived) {
    if (!archived || !archived.length) return "";
    let html = `<button type="button" class="archline dim" data-archived aria-expanded="${state.archived}">
      ${state.archived ? "▾" : "▸"} Archived (${archived.length})</button>`;
    if (state.archived) html += `<div class="archived">${archived.map((t) => `<div class="arow">
        <a href="#${t.id}" class="t" data-id="${t.id}">${md.esc(t.title || "(untitled)")}</a>
        <button type="button" class="ghost small" data-unarchive="${t.id}">Unarchive</button></div>`).join("")}</div>`;
    return html;
  }

  // list: GET /api/threads; groups: GET /api/groups; archived: GET /api/threads?archived=1 (optional).
  function render(list, cur, groups, archived) {
    const own = [], pinned = [], grouped = [], inProject = [], loose = [];
    for (const t of list) {
      if (t.own && t.needs_you) own.push(t);
      else if (t.pinned) pinned.push(t);
      else if (t.group) grouped.push(t);
      else if (t.project) inProject.push(t);
      else loose.push(t);
    }
    let html = own.map((t) => threadLink(t, cur)).join("");
    if (pinned.length) html += `<div class="tgroup pinned">Pinned</div>${pinned.map((t) => threadLink(t, cur)).join("")}`;
    html += chatGroups(grouped, cur, groups);
    html += projects(inProject, cur);
    let last = null;
    for (const t of loose) {
      const g = group(t.updated);
      if (g !== last) { html += `<div class="tgroup">${g}</div>`; last = g; }
      html += threadLink(t, cur);
    }
    if (!html) html = '<div class="dim pad">No threads yet.</div>';
    return html + archivedLine(archived);
  }

  window.sidelist = { render, threadLink, group, opened, gopened, state };
})();
