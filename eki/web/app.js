// eki's web UI. Everything comes from the engine's API; nothing is kept here
// that the engine doesn't have, so a refresh or an engine restart loses nothing.
(function () {
  const $ = (id) => document.getElementById(id);
  if (/EkiMac/.test(navigator.userAgent)) document.documentElement.dataset.shell = "mac";
  const state = { thread: null, after: 0, polling: false, providers: [], offline: false };

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

  async function loadThreads() {
    const list = await call("/api/threads");
    $("threads").innerHTML = list.map((t) => `
      <a href="#${t.id}" class="thread ${t.id === state.thread ? "on" : ""}" data-id="${t.id}">
        ${t.working ? '<span class="dot" title="working"></span>' : ""}
        <span class="t">${md.esc(t.title || "(untitled)")}</span>
        <span class="when dim">${t.provider ? md.esc(t.provider) + " · " : ""}${ago(t.updated)}</span>
      </a>`).join("") || '<div class="dim pad">No threads yet.</div>';
  }

  function stepLine(s) {
    if (s.kind === "tool") return `<div class="step"><b>${md.esc(s.name || "")}</b> ${md.esc(s.detail || "")}</div>`;
    if (s.kind === "handoff") return `<div class="step warn">↪ handed off: ${md.esc(s.reason || "needs tools")}</div>`;
    if (s.kind === "interrupted") return `<div class="step warn">interrupted — resuming</div>`;
    return `<div class="step warn">${md.esc(s.text || s.kind)}</div>`;
  }

  function runHtml(r) {
    const live = ["queued", "starting", "running"].includes(r.state);
    const steps = r.steps.length
      ? `<details class="steps" ${live ? "open" : ""}><summary>${r.steps.length} step${r.steps.length > 1 ? "s" : ""}</summary>${r.steps.map(stepLine).join("")}</details>` : "";
    const who = r.provider ? `<span class="who">${md.esc(r.provider)}</span>` : "";
    const why = r.why ? `<span class="why" title="${md.esc(r.why)}">${md.esc(r.why)}</span>` : "";
    let tail = "";
    if (live) tail = `<div class="live"><span class="spin"></span>${r.state === "queued" ? "waiting" : "working"}
                        <button class="ghost small" data-cancel="${r.id}">Stop</button></div>`;
    if (r.state === "failed") tail = `<div class="err">Failed: ${md.esc(r.error || "")}</div>`;
    if (r.state === "cancelled") tail = `<div class="dim">Stopped.</div>`;
    const answer = r.state === "handed_off" ? "" : md.render(r.answer);
    return `<div class="turn" id="run-${r.id}">
        ${r.parent ? "" : `<div class="user">${md.render(r.prompt)}</div>`}
        <div class="bot"><div class="route">${who}${why}</div>${steps}
          <div class="answer">${answer}</div>${tail}</div></div>`;
  }

  async function loadThread(scroll) {
    if (!state.thread) return;
    const t = await call("/api/threads/" + state.thread);
    $("title").textContent = t.title || "(untitled)";
    $("meta").textContent = [t.provider && "with " + t.provider, t.cwd].filter(Boolean).join(" · ");
    if (t.cwd && !$("cwd").value) $("cwd").value = t.cwd;
    const log = $("log");
    const nearBottom = log.parentElement.scrollHeight - log.parentElement.scrollTop - log.parentElement.clientHeight < 80;
    log.innerHTML = t.runs.map(runHtml).join("");
    $("empty").style.display = t.runs.length ? "none" : "";
    state.after = Math.max(state.after, ...t.runs.map((r) => r.last_event || 0));
    if (scroll || nearBottom) log.parentElement.scrollTop = log.parentElement.scrollHeight;
    const active = t.runs.some((r) => ["queued", "starting", "running"].includes(r.state));
    if (active) poll();
  }

  async function poll() {
    if (state.polling || !state.thread) return;
    state.polling = true;
    const tid = state.thread;
    try {
      while (state.thread === tid) {
        const got = await call(`/api/threads/${tid}/events?after=${state.after}&wait=20`);
        if (state.thread !== tid) break;
        if (got.events.length) {
          state.after = got.events[got.events.length - 1].id;
          await loadThread(false);
          loadThreads();
        }
        if (!got.active) { await loadThread(false); loadThreads(); break; }
      }
    } catch (e) {
      setTimeout(() => { state.polling = false; poll(); }, 1500);   // engine restarting: try again
      return;
    }
    state.polling = false;
  }

  function open(id) {
    state.thread = id || null;
    state.after = 0;
    $("log").innerHTML = "";
    $("empty").style.display = "";
    $("cwd").value = "";
    if (!id) { $("title").textContent = "New thread"; $("meta").textContent = ""; }
    loadThreads();
    loadThread(true);
    $("prompt").focus();
  }

  // ---- providers and the machine ---------------------------------------------------------

  async function loadProviders() {
    let ps, st;
    try {
      [ps, st] = await Promise.all([call("/api/providers"), call("/api/status")]);
    } catch (e) {
      $("engine").textContent = "engine unreachable — reconnecting…";
      state.offline = true;
      return;
    }
    if (state.offline) { state.offline = false; loadThreads(); loadThread(false); }
    state.providers = ps;
    const sel = $("to"), cur = sel.value;
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
        <span class="pname">${md.esc(p.name)}</span><span class="dim pwhy" title="${md.esc(label)}">${md.esc(label)}</span>${action}</div>`;
    }).join("");
    $("engine").textContent = `${st.running} running · ${st.queued} queued · ${st.room ? "room for background work" : st.room_why}`;
  }

  // ---- events ----------------------------------------------------------------------------

  $("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = $("prompt").value.trim();
    if (!prompt) return;
    $("send").disabled = true;
    try {
      const got = await call("/api/ask", {
        prompt, thread: state.thread, to: $("to").value, cwd: $("cwd").value.trim(),
        background: $("bg").checked,
      });
      $("prompt").value = "";
      autosize();
      if (got.thread !== state.thread) { location.hash = got.thread; } else { await loadThread(true); }
    } catch (err) {
      alertLine(err.message);
    } finally {
      $("send").disabled = false;
    }
  });

  function alertLine(text) {
    const d = document.createElement("div");
    d.className = "err pad";
    d.textContent = text;
    $("log").appendChild(d);
  }

  $("prompt").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  function autosize() { const t = $("prompt"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 240) + "px"; }
  $("prompt").addEventListener("input", autosize);

  document.addEventListener("click", async (e) => {
    const c = e.target.closest("[data-cancel]");
    if (c) { await call(`/api/runs/${c.dataset.cancel}/cancel`, {}); loadThread(false); }
    const m = e.target.closest("[data-model]");
    if (m) { m.disabled = true; await call(`/api/models/${m.dataset.model}/${m.dataset.act}`, {}).catch((x) => alertLine(x.message)); loadProviders(); }
  });
  $("new").addEventListener("click", () => { location.hash = ""; open(null); });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); location.hash = ""; open(null); }
  });
  window.addEventListener("hashchange", () => open(location.hash.slice(1) || null));

  // The Mac window moves when dragged by its title areas; tell it where they are.
  function reportDrag() {
    const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.eki;
    if (!h) return;
    const box = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
    h.postMessage({
      drag: [...document.querySelectorAll("[data-drag]")].map(box),
      nodrag: [...document.querySelectorAll("[data-drag] button, [data-drag] a, [data-drag] input, [data-drag] select")].map(box),
    });
  }
  new ResizeObserver(reportDrag).observe(document.body);
  document.querySelectorAll("[data-drag]").forEach((el) => new ResizeObserver(reportDrag).observe(el));
  window.addEventListener("resize", reportDrag);
  reportDrag();

  open(location.hash.slice(1) || null);
  loadProviders();
  setInterval(loadProviders, 5000);
  setInterval(loadThreads, 4000);
})();
