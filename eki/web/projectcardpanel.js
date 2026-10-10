// An open card under the Cards grid (projectcards.js opens it): its settings as a form built from
// component.json (each change saved and checked by its kind — a problem shows beside the field and
// the saved value comes back), an agent's prompt.md and its bare switch, Run (eki use <name> <args>
// through the engine, polled every second), its last 20 uses, and an agent's sessions. A card from
// the pool shows its settings read-only, with a link to edit them in Settings. Routes: eki/api_card*.py.
// A kind with a part of its own (window.projectCardKinds[kind] = {title, draw(el, card, pid, ro), close()},
// from cardknowledge.js, cardmcp.js) gets a section under its title; passive kinds have no Run or Uses.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  const PASSIVE = ["knowledge", "mcp"];                    // eki/components PASSIVE: read or attached, never called
  for (const f of ["cardknowledge.js", "cardmcp.js"]) {
    if (document.querySelector && !document.querySelector(`script[src="/ui/${f}"]`)) {
      const t = document.createElement("script");
      t.src = "/ui/" + f; t.async = false;
      document.head.appendChild(t);
    }
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const P = { el: null, pid: null, card: null, ro: "", ctx: null, last: {}, problems: {}, err: {},
              run: null, uses: null, use: null, useData: null, sessions: null, sess: null, sessData: null, shots: {} };

  const url = (tail) => "/api/projects/" + encodeURIComponent(P.pid) + "/cards/" + encodeURIComponent(P.card.name) + (tail || "");
  const q = (sel) => P.el && P.el.querySelector(sel);
  function put(sel, html) {
    const el = q(sel);
    if (el && P.last[sel] !== html) { el.innerHTML = html; P.last[sel] = html; }
  }
  const ago = (t) => P.ctx ? P.ctx.ago(t) : "";
  const editable = () => !P.ro && P.card.editable && !P.card.ref;
  const isAgent = () => P.card.kind === "agent";
  const passive = () => PASSIVE.includes(P.card.kind);
  const kindPart = () => window.projectCardKinds[P.card.kind] || null;
  const secs = (s) => s == null || s === "" ? "" : `${Math.round(Number(s) * 10) / 10} s`;

  // ---- settings -------------------------------------------------------------------------------

  function fields() {
    const spec = P.card.spec || {};
    const keys = Object.keys(spec).filter((k) => k !== "kind" && k !== "pool");
    if (isAgent() && !keys.includes("bare")) keys.push("bare");
    return keys.map((k) => ({ key: k, value: k === "bare" && !(k in spec) ? false : spec[k] }));
  }

  function control(f, off) {
    const v = f.value, dis = off ? " disabled" : "", at = `data-key="${esc(f.key)}"${dis}`;
    if (typeof v === "boolean") return `<label class="set-check"><input type="checkbox" ${at} data-type="bool"${v ? " checked" : ""}> ${v ? "true" : "false"}</label>`;
    if (typeof v === "number") return `<input class="set-in" type="number" step="any" ${at} data-type="number" value="${esc(v)}">`;
    if (Array.isArray(v)) return `<textarea class="set-in pp-list" rows="${Math.min(8, Math.max(2, v.length + 1))}" ${at} data-type="list" spellcheck="false">${esc(v.join("\n"))}</textarea>`;
    if (v && typeof v === "object") return `<textarea class="set-in pp-list" rows="4" ${at} data-type="json" spellcheck="false">${esc(JSON.stringify(v, null, 2))}</textarea>`;
    return `<input class="set-in" ${at} data-type="text" value="${esc(v == null ? "" : v)}" spellcheck="false">`;
  }

  function settings() {
    const c = P.card;
    if (!c.spec) return c.error ? "" : '<div class="dim">No settings.</div>';
    const off = !editable();
    const note = c.ref ? `<div class="pj-sub">From the pool component <b>${esc(c.ref)}</b> — read-only here.
        <a href="#settings/components">edit in pool</a></div>`
      : P.ro ? `<div class="pj-sub">Read-only: ${esc(P.ro)}.</div>` : "";
    const rows = fields().map((f) => `<div class="pp-field">
        <div class="pp-label">${esc(f.key)}${Array.isArray(f.value) ? ' <span class="dim">one per line</span>' : ""}</div>
        <div class="pp-ctl">${control(f, off)}${P.problems[f.key] ? `<div class="pj-err">${esc(P.problems[f.key])}</div>` : ""}</div></div>`);
    return `${note}<div class="pp-field"><div class="pp-label">kind</div><div class="pp-ctl"><code>${esc(c.kind || "?")}</code></div></div>
      ${rows.join("")}${P.err.settings ? `<div class="pj-err">${esc(P.err.settings)}</div>` : ""}`;
  }

  function valueOf(el) {
    const t = el.dataset.type;
    if (t === "bool") return el.checked;
    if (t === "number") return el.value.trim() === "" ? null : Number(el.value);
    if (t === "list") return el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (t === "json") return JSON.parse(el.value);
    return el.value;
  }

  async function saveSetting(el) {
    const key = el.dataset.key;
    let value;
    try { value = valueOf(el); } catch (x) { P.problems[key] = "not valid JSON: " + x.message; return redrawSettings(); }
    delete P.problems[key]; delete P.err.settings;
    try {
      const got = await P.ctx.api(url(), { settings: { [key]: value } });
      if (got.card && typeof got.card === "object") P.card = got.card;
    } catch (x) {
      const pr = x.problems || {};
      if (Object.keys(pr).length) Object.assign(P.problems, pr); else P.problems[key] = x.message;
    }
    redrawSettings();                          // a refused value goes back to the saved one
    P.ctx.reload();
  }
  // drawn again even when its html is the same, so a refused value shows the saved one; the field
  // you moved on to keeps the focus
  function redrawSettings() {
    const a = document.activeElement, key = a && a.dataset && a.closest && a.closest(".pp-settings") ? a.dataset.key : null;
    P.last[".pp-settings"] = null;
    put(".pp-settings", settings());
    const back = key && q(`.pp-settings [data-key="${CSS.escape(key)}"]`);
    if (back) back.focus();
  }

  // ---- an agent's prompt ----------------------------------------------------------------------

  function prompt() {
    if (!isAgent()) return "";
    const off = !editable() ? " disabled" : "";
    return `<div class="pp-label">prompt.md ${off ? "" : '<span class="dim">saved when you leave the box</span>'}</div>
      <textarea class="set-in pp-prompt-text" rows="10" spellcheck="false"${off}>${esc(P.card.prompt || "")}</textarea>`;
  }

  async function savePrompt(el) {
    if (el.value === (P.card.prompt || "")) return;
    delete P.err.prompt;
    try {
      const got = await P.ctx.api(url(), { prompt: el.value });
      if (got.card && typeof got.card === "object") P.card = got.card; else P.card.prompt = el.value;
    } catch (x) {
      P.err.prompt = Object.values(x.problems || {})[0] || x.message;
      const box = q(".pp-prompt-err");
      if (box) box.innerHTML = `<div class="pj-err">${esc(P.err.prompt)}</div>`;
      return;
    }
    const box = q(".pp-prompt-err");
    if (box) box.innerHTML = "";
  }

  // ---- run ------------------------------------------------------------------------------------

  const ended = (d) => !!(d && (d.ended || ["done", "failed", "error", "cancelled", "canceled", "killed"].includes(d.state)));

  function runPart() {
    const r = P.run;
    let out = "";
    if (r && r.err) out = `<div class="pj-err">${esc(r.err)}</div>`;
    else if (r && r.data) {
      const d = r.data;
      out = `<div class="pp-out"><div class="pj-row"><span class="pj-state">${esc(d.state || "")}</span>
          <span class="dim grow">${esc(d.run || r.id)}${d.seconds != null ? " · " + secs(d.seconds) : ""}</span></div>
        ${d.why ? `<div class="dim">${esc(d.why)}</div>` : ""}
        ${ended(d) ? (d.error ? `<div class="pj-err">${esc(d.error)}</div>` : `<div><b>${esc(d.result || "done")}</b></div>`) : ""}
        ${d.text ? `<pre class="pp-text">${esc(d.text)}</pre>` : ""}</div>`;
    } else if (r) out = `<div class="dim">Starting ${esc(r.id || "")}…</div>`;
    return out;
  }

  async function run() {
    const box = q(".pp-args");
    const args = box ? box.value : "";
    clearTimeout(P.run && P.run.timer);
    P.run = { id: "", data: null };
    put(".pp-runout", runPart());
    try {
      const got = await P.ctx.api(url("/run"), { args });
      P.run.id = got.run;
      poll(P.card.name, got.run);
    } catch (x) { P.run.err = x.message; }
    put(".pp-runout", runPart());
  }

  async function poll(name, id) {
    if (!P.card || P.card.name !== name || !P.run || P.run.id !== id) return;
    try { P.run.data = await P.ctx.api(url("/uses/" + encodeURIComponent(id))); } catch (x) { P.run.data = { state: "?", error: x.message, ended: true }; }
    if (!P.run || P.run.id !== id) return;
    put(".pp-runout", runPart());
    if (ended(P.run.data)) { loadUses(); P.ctx.reload(); } else P.run.timer = setTimeout(() => poll(name, id), 1000);
  }

  // ---- uses -----------------------------------------------------------------------------------

  function usesPart() {
    if (!P.uses) return '<div class="dim">Loading…</div>';
    if (P.uses.err) return `<div class="pj-err">${esc(P.uses.err)}</div>`;
    const list = P.uses.list.slice(0, 20);
    if (!list.length) return '<div class="dim">Never used.</div>';
    return list.map((u) => {
      const open = P.use === u.run;
      const d = open ? P.useData : null;
      return `<div class="pj-item pp-line${open ? " on" : ""}" data-use="${esc(u.run)}" role="button">
          <span class="dim pp-when" title="${esc(u.when || "")}">${esc(ago(u.started || u.when))}</span>
          <span class="pp-who">${esc(u.caller || "you")}</span>
          <span class="grow pp-res">${u.error ? `<span class="pj-err">${esc(u.error)}</span>` : esc(u.result || u.state || "")}</span>
          <span class="dim">${esc(secs(u.seconds))}</span></div>
        ${open ? `<div class="pp-detail">${!d ? '<span class="dim">Loading…</span>' : d.err ? `<span class="pj-err">${esc(d.err)}</span>`
          : `${d.why ? `<div class="dim">${esc(d.why)}</div>` : ""}<pre class="pp-text">${esc(d.text || "(no text)")}</pre>`}</div>` : ""}`;
    }).join("");
  }

  async function loadUses() {
    const name = P.card && P.card.name;
    try {
      const got = await P.ctx.api(url("/uses"));
      if (!P.card || P.card.name !== name) return;
      P.uses = { list: got.uses || [] };
    } catch (x) { if (P.card && P.card.name === name) P.uses = { list: [], err: x.message }; }
    put(".pp-uses", usesPart());
  }

  async function openUse(id) {
    if (P.use === id) { P.use = null; P.useData = null; return put(".pp-uses", usesPart()); }
    P.use = id; P.useData = null;
    put(".pp-uses", usesPart());
    try { P.useData = await P.ctx.api(url("/uses/" + encodeURIComponent(id))); } catch (x) { P.useData = { err: x.message }; }
    if (P.use === id) put(".pp-uses", usesPart());
  }

  // ---- an agent's sessions --------------------------------------------------------------------

  function sessionsPart() {
    if (!isAgent()) return "";
    if (!P.sessions) return '<div class="dim">Loading…</div>';
    if (P.sessions.err) return `<div class="pj-err">${esc(P.sessions.err)}</div>`;
    if (!P.sessions.list.length) return '<div class="dim">No sessions yet.</div>';
    return P.sessions.list.map((s) => `<div class="pj-item pp-line${P.sess === s.id ? " on" : ""}" data-sess="${esc(s.id)}" role="button">
        <span class="dim pp-when">${esc(ago(s.when))}</span>
        <span class="grow pp-res">${esc((s.review || "").split("\n")[0] || "(no review)")}</span>
        ${s.tainted ? '<span class="pj-err" title="tainted">tainted</span>' : ""}</div>
      ${P.sess === s.id ? `<div class="pp-detail">${session()}</div>` : ""}`).join("");
  }

  function session() {
    const d = P.sessData;
    if (!d) return '<span class="dim">Loading…</span>';
    if (d.err) return `<span class="pj-err">${esc(d.err)}</span>`;
    const shots = (d.screenshots || []).map((x) => typeof x === "string" ? x : x.name || x.path || "");
    return `${d.tainted ? `<div class="pj-err">tainted${d.taint ? ": " + esc(typeof d.taint === "string" ? d.taint : JSON.stringify(d.taint)) : ""}</div>` : ""}
      <div class="pj-sub">Review</div><pre class="pp-text">${esc(d.review || "(none)")}</pre>
      ${d.notes ? `<div class="pj-sub">Notes</div><pre class="pp-text">${esc(d.notes)}</pre>` : ""}
      ${(d.emotions || []).length ? `<div class="pj-sub">Emotions</div><div class="pp-tl">${d.emotions.map((m) =>
        `<div class="pp-tick"><span class="dim pp-t">${esc(m.t)}</span><b>${esc(m.emotion)}</b> <span class="dim">${esc(m.why || "")}</span></div>`).join("")}</div>` : ""}
      ${shots.length ? `<div class="pj-sub">Screenshots</div><div class="pj-grid">${shots.map((n) =>
        `<a class="pp-shot" data-shot="${esc(n)}" title="${esc(n)}" href="#" >${P.shots[n] ? `<img src="${esc(P.shots[n])}" alt="${esc(n)}">` : `<span class="dim">${esc(n)}</span>`}</a>`).join("")}</div>` : ""}`;
  }

  async function loadSessions() {
    if (!isAgent()) return;
    const name = P.card.name;
    try {
      const got = await P.ctx.api(url("/sessions"));
      if (!P.card || P.card.name !== name) return;
      P.sessions = { list: got.sessions || [] };
    } catch (x) { if (P.card && P.card.name === name) P.sessions = { list: [], err: x.message }; }
    put(".pp-sessions", sessionsPart());
  }

  async function openSession(id) {
    if (P.sess === id) { P.sess = null; P.sessData = null; return put(".pp-sessions", sessionsPart()); }
    P.sess = id; P.sessData = null;
    put(".pp-sessions", sessionsPart());
    try { P.sessData = await P.ctx.api(url("/sessions/" + encodeURIComponent(id))); } catch (x) { P.sessData = { err: x.message }; }
    if (P.sess !== id) return;
    put(".pp-sessions", sessionsPart());
    for (const x of P.sessData.screenshots || []) {
      const n = typeof x === "string" ? x : x.name || x.path || "";
      if (!n || P.shots[n]) continue;
      try {
        const f = await P.ctx.api(url("/sessions/" + encodeURIComponent(id) + "/file?name=" + encodeURIComponent(n)));
        if (f.data_url) P.shots[n] = f.data_url;
      } catch (e) { /* the name stays as the thumbnail */ }
      if (P.sess === id) put(".pp-sessions", sessionsPart());
    }
  }

  // ---- the panel ------------------------------------------------------------------------------

  function head() {
    const c = P.card;
    return `<div class="pj-row"><b class="pc-pname">${esc(c.name)}</b><span class="dim">${esc(c.kind || "")}</span>
        <span class="pc-state">${esc(c.error ? "broken" : c.state || "idle")}</span><span class="grow"></span>
        <button type="button" class="pj-btn" data-pp="close">Close</button></div>
      ${c.about ? `<div class="pj-sub">${esc(c.about)}</div>` : ""}
      ${c.error ? `<div class="pj-err">${esc(c.error)}</div>` : ""}
      ${(c.kind === "skills" ? [] : c.notes || []).map((n) => `<div class="dim">${esc(n)}</div>`).join("")}
      <div class="dim pp-path"><code>${esc(c.card || c.folder || "")}</code></div>`;
  }

  function frame() {
    const agent = isAgent(), part = kindPart();
    P.framed = part;
    P.el.innerHTML = `<div class="pj-card pp">
      <div class="pp-head"></div>
      ${part ? `<h4 class="pp-h">${esc(part.title)}</h4><div class="pp-kind"></div>` : ""}
      <h4 class="pp-h">Settings</h4><div class="pp-settings"></div>
      ${agent ? '<h4 class="pp-h">Prompt</h4><div class="pp-prompt"></div><div class="pp-prompt-err"></div>' : ""}
      ${P.card.kind === "skills" ? '<h4 class="pp-h">Skills</h4><div class="pp-skills"></div>' : ""}
      ${passive() ? "" : `<h4 class="pp-h">Run</h4>
      <form class="pj-row pp-runform" autocomplete="off"><input class="grow pp-args" placeholder="args or focus for eki use ${esc(P.card.name)}" spellcheck="false"${P.ro ? " disabled" : ""}>
        <button type="submit" class="pj-btn primary"${P.ro ? " disabled" : ""}>Run</button></form>
      <div class="pp-runout"></div>
      <h4 class="pp-h">Uses <span class="dim">last 20</span></h4><div class="pp-uses"></div>`}
      ${agent ? '<h4 class="pp-h">Sessions</h4><div class="pp-sessions"></div>' : ""}</div>`;
    P.last = {};
  }

  function draw() {
    if (kindPart() !== P.framed) frame();         // a kind's part registered after the card opened
    put(".pp-head", head());
    const part = kindPart(), box = q(".pp-kind");
    if (part && box) part.draw(box, P.card, P.pid, P.ro);
    const a = document.activeElement;
    const typing = a && P.el.contains(a) && a.matches && a.matches("input, textarea");
    if (!(typing && a.closest(".pp-settings"))) put(".pp-settings", settings());
    if (!(typing && a.closest(".pp-prompt"))) put(".pp-prompt", prompt());
    put(".pp-runout", runPart());
    put(".pp-uses", usesPart());
    put(".pp-sessions", sessionsPart());
    if (P.card.kind === "skills" && window.projectCardSkills) {   // projectcardskills.js: its list and Add skill
      projectCardSkills.draw(q(".pp-skills"), P.card, { api: P.ctx.api, url, ro: !editable(),
        done: (card) => { if (card) { P.card = card; draw(); } P.ctx.reload(); } });
    }
  }

  function onEvent(e) {
    if (!P.card) return;                       // an instructions card: projectinstructions.js has it
    const t = e.target;
    if (e.type === "submit") { e.preventDefault(); return t.matches(".pp-runform") && run(); }
    if (e.type === "change" && t.dataset && t.dataset.key) return saveSetting(t);
    if (e.type === "focusout" && t.matches && t.matches(".pp-prompt-text")) return savePrompt(t);
    if (e.type !== "click") return;
    if (t.closest("[data-pp]")) return P.ctx.close();
    const shot = t.closest("[data-shot]");
    if (shot) { e.preventDefault(); return shot.classList.toggle("big"); }
    if (t.closest(".pp-detail")) return;
    const u = t.closest("[data-use]");
    if (u) return openUse(u.dataset.use);
    const s = t.closest("[data-sess]");
    if (s) return openSession(s.dataset.sess);
  }

  function open(el, pid, card, ro, ctx) {
    close();
    if (!el) return;
    if (card.kind === "instructions" && window.projectInstructions) return projectInstructions.open(el, pid, card, ro, ctx);
    Object.assign(P, { el, pid, card, ro, ctx, problems: {}, err: {}, run: null, uses: null, use: null, useData: null,
                       sessions: null, sess: null, sessData: null, shots: {} });
    if (!el.dataset.ppWired) {
      el.dataset.ppWired = "1";
      for (const ev of ["click", "change", "focusout", "submit"]) el.addEventListener(ev, onEvent);
    }
    frame();
    draw();
    if (!passive()) loadUses();
    loadSessions();
  }

  // the cards reloaded: the card as it is now
  function update(card, ro) {
    if (card.kind === "instructions" && window.projectInstructions) return projectInstructions.update(card, ro);
    if (!P.card || !P.el || card.name !== P.card.name) return;
    P.card = card; P.ro = ro;
    draw();
    if (!passive()) loadUses();
    if (P.sessions && !P.sess) loadSessions();
  }

  function close() {
    window.projectInstructions && projectInstructions.close();
    const part = P.card && kindPart();
    if (part && part.close) part.close();
    if (P.run) clearTimeout(P.run.timer);
    if (P.el) P.el.innerHTML = "";
    Object.assign(P, { card: null, run: null, last: {}, framed: null });
  }

  // a kind's part arrived: the open card drawn again with it
  function redraw() { if (P.card && P.el) draw(); }

  window.projectCardPanel = { open, update, close, redraw };
})();
