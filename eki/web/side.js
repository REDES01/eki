// The sidebar: chats in a project under its header, the rest grouped by when they last moved, and at the bottom the
// providers, the machine and the latest digest. All of it from the API.
(function () {
  const $ = (id) => document.getElementById(id);
  let offline = false;

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

  function ago(t) {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    return Math.floor(s / 86400) + "d";
  }

  // ---- threads ---------------------------------------------------------------------------

  function group(t) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = today.getTime() / 1000;
    if (t >= day) return "Today";
    if (t >= day - 86400) return "Yesterday";
    if (t >= day - 7 * 86400) return "Previous 7 days";
    return "Older";
  }

  function threadLink(t, cur) {
    return `
      <a href="#${t.id}" class="thread ${t.id === cur ? "on" : ""}" data-id="${t.id}">
        ${t.needs_you ? '<span class="dot you" title="needs you"></span>' : t.working ? '<span class="dot" title="working"></span>' : ""}
        <span class="t">${md.esc(t.title || "(untitled)")}</span>
        <span class="when dim">${t.provider ? md.esc(t.provider) + " · " : ""}${ago(t.updated)}</span>
      </a>`;
  }

  // Project groups you opened or closed: what the page shows, not keeps. Unset, the current thread's is open.
  const opened = new Map();

  // Chats in a project under its header (projects by their latest chat), then the rest by date.
  function render(list, cur) {
    const byProject = new Map(), loose = [];
    for (const t of list) {
      if (!t.project) { loose.push(t); continue; }
      if (!byProject.has(t.project.id)) byProject.set(t.project.id, { p: t.project, chats: [] });
      byProject.get(t.project.id).chats.push(t);
    }
    let html = "";
    const groups = [...byProject.values()];
    groups.forEach((g) => g.chats.sort((a, b) => b.updated - a.updated));
    groups.sort((a, b) => b.chats[0].updated - a.chats[0].updated);
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
    let last = null;
    for (const t of loose) {
      const g = group(t.updated);
      if (g !== last) { html += `<div class="tgroup">${g}</div>`; last = g; }
      html += threadLink(t, cur);
    }
    return html || '<div class="dim pad">No threads yet.</div>';
  }

  let lastList = [];
  async function loadThreads() {
    lastList = await call("/api/threads");
    $("threads").innerHTML = render(lastList, nav.current());
  }

  // ---- providers and the machine ---------------------------------------------------------

  const WINDOW = { five_hour: "5h", seven_day: "week", thirty_day: "30 days" };
  function meters(q) {
    if (!q || !q.windows) return "";
    return `<div class="meters">${Object.entries(q.windows).map(([k, w]) => {
      const pct = Math.round((w.used || 0) * 100);
      const resets = w.resets_at ? new Date(w.resets_at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
      return `<div class="meter" title="${WINDOW[k] || k}: ${pct}% used${resets ? " · resets " + resets : ""}">
        <span class="mlabel">${WINDOW[k] || k}</span><span class="mbar"><i style="width:${Math.min(pct, 100)}%" class="${pct >= 100 ? "full" : ""}"></i></span><span class="mpct">${pct}%</span></div>`;
    }).join("")}</div>`;
  }

  // The latest daily digest, a line under the engine; a click opens it in the panel (panel.js).
  function showDigest(path) {
    let el = $("digest");
    if (!path) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("button");
      el.type = "button"; el.id = "digest"; el.className = "file ghost small";
      $("machine").appendChild(el);
    }
    el.dataset.file = path;
    el.textContent = "Digest " + (path.split("/").pop() || "").replace(/\.md$/, "");
  }

  async function loadProviders() {
    let ps, st;
    try {
      [ps, st] = await Promise.all([call("/api/providers"), call("/api/status")]);
    } catch (e) {
      $("engine").textContent = "engine unreachable — reconnecting…";
      offline = true;
      return;
    }
    if (offline) { offline = false; window.dispatchEvent(new Event("eki:refresh")); }
    const sel = $("to"), cur = sel.value;   // who answers, in the composer
    sel.innerHTML = '<option value="">Auto</option>' +
      ps.map((p) => `<option value="${p.name}">${md.esc(p.label)}</option>`).join("");
    sel.value = cur;
    $("providers").innerHTML = ps.map((p) => {
      const m = p.model;
      let action = "";
      if (m && m.startable) action = m.up || m.starting
        ? `<button class="ghost small" data-model="${p.name}" data-act="stop">Stop</button>`
        : `<button class="ghost small" data-model="${p.name}" data-act="start">Start</button>`;
      const label = m && m.starting ? "starting…" : (p.ok ? "ready" : p.why);
      return `<div class="prov"><span class="led ${p.ok ? "ok" : m && m.starting ? "wait" : ""}"></span>
        <span class="pname">${md.esc(p.name)}</span><span class="dim pwhy" title="${md.esc(label)}">${p.quota ? (p.quota.plan || "") : md.esc(label)}</span>${action}</div>${meters(p.quota)}`;
    }).join("");
    showDigest(st.digest);
    const pr = $("prs");   // one line per project with PRs open; hidden when none
    if (pr) { pr.innerHTML = prs.lines(st.prs); pr.hidden = !prs.total(st.prs); }
    $("engine").textContent = `${st.running} running · ${st.queued} queued · ${st.room ? "room for background work" : st.room_why}`;
  }

  document.addEventListener("click", async (e) => {
    const pg = e.target.closest("[data-pgroup]");
    if (pg) {   // a project header opens to its chats, or closes
      const id = pg.dataset.pgroup;
      opened.set(id, !pg.closest(".pgroup").classList.contains("open"));
      $("threads").innerHTML = render(lastList, nav.current());
      return;
    }
    const m = e.target.closest("[data-model]");
    if (!m) return;
    m.disabled = true;
    await call(`/api/models/${m.dataset.model}/${m.dataset.act}`, {}).catch((x) => { $("engine").textContent = x.message; });
    loadProviders();
  });
  window.addEventListener("eki:view", () => loadThreads().catch(() => {}));

  loadProviders();
  setInterval(loadProviders, 5000);
  setInterval(() => loadThreads().catch(() => {}), 4000);
  window.side = { loadThreads, loadProviders, render };
})();
