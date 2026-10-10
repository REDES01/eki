// The sidebar's bottom row: a status dot (green when every provider is ok, amber otherwise) and "n running".
// A click opens a popover with the providers, the machine and the latest digest, as the sidebar showed them
// before; Esc or a click outside closes it. It also fills the composer's "Who answers" list. All from the API.
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

  // one line per provider: its light, name, plan or why, a Start/Stop for a local model, and its quota meters
  function providers(ps) {
    return ps.map((p) => {
      const m = p.model;
      let action = "";
      if (m && m.startable) action = m.up || m.starting
        ? `<button class="ghost small" data-model="${p.name}" data-act="stop">Stop</button>`
        : `<button class="ghost small" data-model="${p.name}" data-act="start">Start</button>`;
      const label = m && m.starting ? "starting…" : (p.ok ? "ready" : p.why);
      return `<div class="prov"><span class="led ${p.ok ? "ok" : m && m.starting ? "wait" : ""}"></span>
        <span class="pname">${md.esc(p.name)}</span><span class="dim pwhy" title="${md.esc(label)}">${p.quota ? (p.quota.plan || "") : md.esc(label)}</span>${action}</div>${meters(p.quota)}`;
    }).join("");
  }

  // the dot: green when every provider is ok, amber otherwise
  const dot = (ps) => (ps.length && ps.every((p) => p.ok) ? "ok" : "warn");

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

  function draw(ps, st) {
    const sel = $("to"), cur = sel.value;   // who answers, in the composer
    sel.innerHTML = '<option value="">Auto</option>' +
      ps.map((p) => `<option value="${p.name}">${md.esc(p.label)}</option>`).join("");
    sel.value = cur;
    $("providers").innerHTML = providers(ps);
    showDigest(st.digest);
    const pr = $("prs");   // one line per project with PRs open; hidden when none
    if (pr) { pr.innerHTML = prs.lines(st.prs); pr.hidden = !prs.total(st.prs); }
    $("engine").textContent = `${st.running} running · ${st.queued} queued · ${st.room ? "room for background work" : st.room_why}`;
    $("status-dot").className = "led " + dot(ps);
    $("status-text").textContent = `${st.running} running`;
  }

  async function load() {
    let ps, st;
    try {
      [ps, st] = await Promise.all([call("/api/providers"), call("/api/status")]);
    } catch (e) {
      $("engine").textContent = "engine unreachable — reconnecting…";
      $("status-dot").className = "led warn";
      $("status-text").textContent = "offline";
      offline = true;
      return;
    }
    if (offline) { offline = false; window.dispatchEvent(new Event("eki:refresh")); }
    draw(ps, st);
  }

  // ---- the popover -----------------------------------------------------------------------

  const isOpen = () => !$("status-pop").hidden;
  function open() { $("status-pop").hidden = false; $("status").setAttribute("aria-expanded", "true"); }
  function close() { $("status-pop").hidden = true; $("status").setAttribute("aria-expanded", "false"); }

  document.addEventListener("click", async (e) => {
    if (e.target.closest("#status")) return isOpen() ? close() : open();
    if (isOpen() && !e.target.closest("#status-pop")) close();
    const m = e.target.closest("[data-model]");
    if (!m) return;
    m.disabled = true;
    await call(`/api/models/${m.dataset.model}/${m.dataset.act}`, {}).catch((x) => { $("engine").textContent = x.message; });
    load();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });

  load();
  setInterval(load, 5000);
  window.sidestatus = { load, draw, providers, dot, open, close, isOpen };
})();
