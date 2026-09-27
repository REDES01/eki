// eki's chat: the thread view and the composer (nav.js routes, side.js is the sidebar).
// Everything comes from the engine's API; nothing is kept here
// that the engine doesn't have, so a refresh or an engine restart loses nothing.
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { thread: null, after: 0, polling: false };

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
    const waiting = (r.asks || []).some((a) => a.state === "open");
    if (live) tail = `<div class="live"><span class="spin ${waiting ? "paused" : ""}"></span>${waiting ? "waiting for you" : r.state === "queued" ? "waiting" : "working"}
                        <button class="ghost small" data-cancel="${r.id}">Stop</button></div>`;
    if (r.state === "failed") tail = `<div class="err">Failed: ${md.esc(r.error || "")}</div>`;
    if (r.state === "cancelled") tail = `<div class="dim">Stopped.</div>`;
    const answer = r.state === "handed_off" ? "" : md.render(r.answer);
    return `<div class="turn" id="run-${r.id}">
        ${r.parent ? "" : `<div class="user">${ekiAttach.thumbs(r.attachments)}${md.render(r.prompt)}</div>`}
        <div class="bot"><div class="route">${who}${why}</div>${steps}
          <div class="answer">${answer}</div>${(r.asks || []).map(cards.render).join("")}${files(r)}${tail}</div></div>`;
  }

  function files(r) {
    if (!r.files || !r.files.length) return "";
    const pic = (p) => /\.(png|jpe?g|gif|webp)$/i.test(p);
    const imgs = r.files.filter(pic).map((p) => `<button type="button" class="pic" data-file="${md.esc(p)}" title="${md.esc(p)}">
      <img src="/api/file?path=${encodeURIComponent(p)}" alt="${md.esc(p.split("/").pop())}" loading="lazy"></button>`).join("");
    return `${imgs}<div class="files">${r.files.map((p) => `<button type="button" class="file" data-file="${md.esc(p)}">
      ${md.esc(p.split("/").pop())}</button>`).join("")}</div>`;
  }

  async function loadThread(scroll) {
    const tid = state.thread;
    if (!tid) return;
    const t = await call("/api/threads/" + tid);
    if (state.thread !== tid) return;   // the page moved on while this was loading
    $("title").textContent = t.title || "(untitled)";
    $("meta").textContent = [t.provider && "with " + t.provider, t.cwd].filter(Boolean).join(" · ");
    if (t.cwd && !$("cwd").value) $("cwd").value = t.cwd;
    const log = $("log");
    if (log.contains(document.activeElement) && document.activeElement.matches(".ask input")) return;  // don't wipe a half-typed answer
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
          side.loadThreads();
        }
        if (!got.active) { await loadThread(false); side.loadThreads(); break; }
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
    loadThread(true);
    $("prompt").focus();
  }

  // ---- events ----------------------------------------------------------------------------

  $("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = $("prompt").value.trim();
    if (!prompt) return;
    if (ekiAttach.busy()) return alertLine("A picture is still uploading.");
    $("send").disabled = true;
    try {
      const got = await call("/api/ask", {
        prompt, thread: state.thread, to: $("to").value, cwd: $("cwd").value.trim(),
        background: $("bg").checked, attachments: ekiAttach.paths(),
      });
      $("prompt").value = "";
      ekiAttach.clear();
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
  });
  window.addEventListener("eki:refresh", () => { loadThread(false); side.loadThreads(); });

  // Leaving the chat for another view (nav.js) stops following the thread.
  window.addEventListener("eki:view", (e) => { if (e.detail !== "chat") state.thread = null; });

  nav.thread(open);
  nav.start();
})();
