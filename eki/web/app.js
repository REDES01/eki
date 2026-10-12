// eki's chat: the thread view and the composer (nav.js routes, side.js is the sidebar).
// Everything comes from the engine's API; nothing is kept here that the engine doesn't have, so a refresh or an
// engine restart loses nothing. The one exception is the composer's unsent text and pictures: a draft per
// conversation, kept in this window's localStorage only (ekiDrafts in attach.js), never sent or synced.
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { thread: null, project: null, after: 0, polling: false, answers: {}, whys: new Set(), live: null, draft: null };   // answers/whys: what the page shows, not keeps
  // draft: the key of the conversation the composer's text belongs to (a thread's id, or "new")

  const LIVE = ["queued", "starting", "running"];

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

  // A chat turn's change saved to the project's history (eki/chatcommit.py): a quiet line, and a way back.
  const SAVED = { chat: (t) => "saved to history: " + t, hand: () => "your edits saved", back: (t) => "went back: " + t };
  function commitLine(s, rid) {
    const say = SAVED[s.commit] || SAVED.chat;
    const back = rid && (s.commit === "chat" || s.commit === "hand")
      ? ` <button type="button" class="ghost small" data-back="${md.esc(rid)}" data-sha="${md.esc(s.sha || "")}">go back to before this</button>` : "";
    return `<div class="step dim" title="${md.esc(s.sha || "")}">${md.esc(say(s.title || ""))}${back}</div>`;
  }

  function stepLine(s, rid) {
    if (s.kind === "commit") return commitLine(s, rid);
    if (s.kind === "tool") {
      const detail = md.esc(s.detail || "");                     // a .md path in it opens in the side panel
      return `<div class="step"><b>${md.esc(s.name || "")}</b> ${typeof md.linkPaths === "function" ? md.linkPaths(detail) : detail}</div>`;
    }
    if (s.kind === "handoff") return `<div class="step warn">↪ handed off: ${md.esc(s.reason || "needs tools")}</div>`;
    if (s.kind === "interrupted") return `<div class="step warn">interrupted — resuming</div>`;
    return `<div class="step warn">${md.esc(s.text || s.kind)}</div>`;
  }

  // A provider's mark: its initial on a colour from its name, the same colour every time.
  function mark(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return `<span class="pmark" style="background: hsl(${h} 45% 46%)">${md.esc(name.charAt(0).toUpperCase())}</span>`;
  }

  // targets.choose()'s notes when a model or effort is no longer offered
  const FELL_BACK = /any more; its default|takes no effort|model picked; its default/;

  function runHtml(r) {
    const live = LIVE.includes(r.state);
    const steps = r.steps.length
      ? `<details class="steps" ${live ? "open" : ""}><summary>${r.steps.length} step${r.steps.length > 1 ? "s" : ""}</summary>${r.steps.map((s) => stepLine(s, r.id)).join("")}</details>` : "";
    // what it went to, as the program names it (`claude · Opus 5.5 · high`); amber when routing fell back
    const fell = FELL_BACK.test(r.why || "");
    const who = r.provider ? `${mark(r.provider)}<span class="who"${fell ? ' style="color: var(--amber)"' : ""}>${md.esc(r.shown || r.provider)}</span>` : "";
    const why = r.why ? `<button type="button" class="why ${state.whys.has(String(r.id)) ? "open" : ""}" data-why="${r.id}"
                          title="Why this provider">${md.esc(r.why)}</button>` : "";
    let tail = "";
    const waiting = (r.asks || []).some((a) => a.state === "open");
    if (live) tail = `<div class="live"><span class="spin ${waiting ? "paused" : ""}"></span>${waiting ? "waiting for you" : r.state === "queued" ? "waiting" : "working"}
                        <button class="ghost small" data-cancel="${r.id}">Stop</button></div>`;
    if (r.state === "failed") tail = `<div class="err">Failed: ${md.esc(r.error || "")}</div>`;
    if (r.state === "cancelled") tail = `<div class="dim">Stopped.</div>`;
    const answer = r.state === "handed_off" ? "" : md.render(r.answer);
    const copy = answer && !live ? `<div class="tools"><button type="button" class="copy" data-copy-run="${r.id}" title="Copy the answer">Copy</button></div>` : "";
    return `<div class="turn" id="run-${r.id}">
        ${r.parent ? "" : `<div class="user">${ekiAttach.thumbs(r.attachments)}${r.prompt ? `<div class="bubble">${md.render(r.prompt)}</div>` : ""}</div>`}
        <div class="bot"><div class="route"${fell ? ' style="color: var(--amber)"' : ""}>${who}${why}</div>${steps}
          <div class="answer">${answer}</div>${(r.asks || []).map(cards.render).join("")}${files(r)}${tail}${copy}</div></div>`;
  }

  // A copy button on each code block, added after md.render.
  function codeButtons(root) {
    root.querySelectorAll(".answer pre").forEach((pre) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "copy code-copy";
      b.textContent = "Copy";
      b.dataset.copyCode = "";
      pre.appendChild(b);
    });
  }

  // navigator.clipboard where the page may use it; a hidden textarea where it may not.
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) return await navigator.clipboard.writeText(text);
    } catch (e) { /* fall through */ }
    const t = document.createElement("textarea");
    t.value = text;
    t.setAttribute("readonly", "");
    t.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }

  function greet() {
    const h = new Date().getHours();
    const part = h < 5 ? "Still up" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    $("empty").innerHTML = `<div class="hello">${part}.</div><div class="dim">Ask anything. eki picks who answers and says why.</div>`;
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
    state.project = t.project || null;   // a project's chat works in its folder; nothing to pick
    if (t.cwd && !$("cwd").value && !state.project) $("cwd").value = t.cwd;
    chip();
    const last = t.runs[t.runs.length - 1];
    sendMode(last && LIVE.includes(last.state) ? last.id : null);
    $("main").classList.toggle("blank", !t.runs.length);
    const log = $("log");
    if (log.contains(document.activeElement) && document.activeElement.matches(".ask input")) return;  // don't wipe a half-typed answer
    const nearBottom = log.parentElement.scrollHeight - log.parentElement.scrollTop - log.parentElement.clientHeight < 80;
    log.innerHTML = t.runs.map(runHtml).join("");
    state.answers = Object.fromEntries(t.runs.map((r) => [r.id, r.answer || ""]));
    codeButtons(log);
    $("empty").style.display = t.runs.length ? "none" : "";
    state.after = Math.max(state.after, ...t.runs.map((r) => r.last_event || 0));
    if (scroll || nearBottom) log.parentElement.scrollTop = log.parentElement.scrollHeight;
    const active = t.runs.some((r) => LIVE.includes(r.state));
    if (active) poll();
  }

  // You've seen this chat: it reads as read in the sidebar until a run ends while you're elsewhere.
  function seen(tid) {
    if (tid) call("/api/threads/" + tid, { unread: false }).then(() => side.loadThreads()).catch(() => {});
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
        if (!got.active) {
          await loadThread(false);
          if (document.visibilityState === "visible") seen(tid); else side.loadThreads();
          break;
        }
      }
    } catch (e) {
      setTimeout(() => { state.polling = false; poll(); }, 1500);   // engine restarting: try again
      return;
    }
    state.polling = false;
  }

  // The composer's draft goes with its conversation: kept under the old key, the new one's put back.
  function swapDraft(key) {
    if (state.draft) ekiDrafts.save(state.draft, $("prompt").value, ekiAttach.paths());
    state.draft = key;
    const d = ekiDrafts.get(key);
    $("prompt").value = d ? d.text : "";
    ekiAttach.restore(d ? d.attachments : []);
    autosize();
  }

  function open(id) {
    swapDraft(id || "new");
    state.thread = id || null;
    state.project = null;
    state.after = 0;
    state.whys.clear();
    $("log").innerHTML = "";
    $("empty").style.display = "";
    greet();
    $("cwd").value = "";
    chip();
    sendMode(null);
    $("main").classList.add("blank");
    if (!id) { $("title").textContent = "New thread"; $("meta").textContent = ""; }
    loadThread(true);
    seen(state.thread);
    $("prompt").focus();
  }

  // ---- events ----------------------------------------------------------------------------

  // Send turns into Stop while the thread's latest run is live; Enter still sends.
  function sendMode(id) {
    state.live = id;
    const b = $("send");
    b.classList.toggle("stop", !!id);
    b.textContent = id ? "■" : "↑";
    b.title = id ? "Stop this run" : "Send (Enter)";
    b.setAttribute("aria-label", id ? "Stop" : "Send");
  }

  // Who answers, folder and background live in the options menu; the chip says what's chosen.
  // A chat in a project has no folder field: it works in the project's folder, and the chip names the project.
  function chip() {
    const to = $("to"), cwd = state.project ? "" : $("cwd").value.trim();
    $("cwd").closest(".mrow").hidden = !!state.project;
    const parts = [to.value ? to.options[to.selectedIndex].text : "Auto"];
    if (state.project) parts.push("📁 " + (state.project.name || state.project.id));
    else if (cwd) parts.push("📁 " + (cwd.replace(/\/+$/, "").split("/").pop() || cwd));
    if ($("bg").checked) parts.push("background");
    $("chip").textContent = parts.join(" · ") + " ▾";
  }
  function menu(show) {
    $("menu").hidden = !show;
    $("chip").setAttribute("aria-expanded", String(show));
  }
  $("chip").addEventListener("click", () => menu($("menu").hidden));
  ["to", "cwd", "bg"].forEach((id) => { $(id).addEventListener("input", chip); $(id).addEventListener("change", chip); });
  new MutationObserver(chip).observe($("to"), { childList: true });   // side.js refills the providers
  document.addEventListener("mousedown", (e) => { if (!e.target.closest("#options")) menu(false); });
  $("menu").addEventListener("keydown", (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); menu(false); $("prompt").focus(); } });

  $("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.live && e.submitter === $("send")) {
      try { await call(`/api/runs/${state.live}/cancel`, {}); } catch (err) { alertLine(err.message); }
      return loadThread(false);
    }
    const prompt = $("prompt").value.trim();
    if (!prompt) return;
    if (ekiAttach.busy()) return alertLine("A picture is still uploading.");
    $("send").disabled = true;
    const key = state.draft;
    try {
      const got = await call("/api/ask", {
        prompt, thread: state.thread, to: $("to").value, cwd: state.project ? "" : $("cwd").value.trim(),
        background: $("bg").checked, attachments: ekiAttach.paths(),
      });
      $("prompt").value = "";
      ekiAttach.clear();
      ekiDrafts.clear(key);
      autosize();
      menu(false);
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
  function autosize() { const t = $("prompt"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 240) + "px"; t.classList.toggle("tall", t.scrollHeight > 240); }
  $("prompt").addEventListener("input", autosize);
  ekiDrafts.bind($("prompt"), () => state.draft, ekiAttach.paths);

  document.addEventListener("click", async (e) => {
    const c = e.target.closest("[data-cancel]");
    if (c) { await call(`/api/runs/${c.dataset.cancel}/cancel`, {}); loadThread(false); return; }
    const g = e.target.closest("[data-back]");
    if (g) {   // a new commit with the earlier files; nothing in the history is lost
      if (!window.confirm("Go back to before this? The folder's files return to how they were; this turn stays in the history.")) return;
      g.disabled = true;
      try { await call(`/api/runs/${g.dataset.back}/back`, { sha: g.dataset.sha }); } catch (err) { alertLine(err.message); }
      return loadThread(false);
    }
    const w = e.target.closest("[data-why]");
    if (w) {   // the route's why: one dim line, the whole of it on a click
      const id = w.dataset.why;
      if (state.whys.has(id)) state.whys.delete(id); else state.whys.add(id);
      w.classList.toggle("open", state.whys.has(id));
      return;
    }
    const b = e.target.closest("[data-copy-run], [data-copy-code]");
    if (b) {
      const code = b.closest("pre");
      await copyText(code ? code.querySelector("code").textContent : state.answers[b.dataset.copyRun] || "");
      b.textContent = "Copied";
      setTimeout(() => { b.textContent = "Copy"; }, 1200);
    }
  });
  window.addEventListener("eki:refresh", () => { loadThread(false); side.loadThreads(); });

  // Leaving the chat for another view (nav.js) stops following the thread.
  window.addEventListener("eki:view", (e) => { if (e.detail !== "chat") state.thread = null; });

  nav.thread(open);
  nav.start();
})();
