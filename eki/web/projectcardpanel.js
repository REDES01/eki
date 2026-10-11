// An open card in the side panel (projectcards.js hosts it there; the panel's header has its name,
// kind and about): its state, settings as a form built from component.json (each change saved and
// checked by its kind — a problem shows beside the field and the saved value comes back; a setting with
// fixed choices, card.choices from its kind, is a choicemenu.js dropdown explaining each choice in its open list,
// card.choice_help), a free-text setting its kind explains (card.field_help) a text field with that line
// under it even when the spec lacks it, an agent's prompt.md and its bare switch,
// Run (eki use <name> <args> through the engine, polled every second), its last 20 uses, and an agent's sessions (cardsessions.js). A card from the pool shows its settings
// read-only, with a link to edit them in Settings. Routes: eki/api_card*.py.
// A kind with a part of its own (window.projectCardKinds[kind] = {title, draw(el, card, pid, ro, ctx), close(), keys},
// from cardknowledge.js, cardmcp.js, cardworkflow.js) gets a section under its title; the settings it lists in
// `keys` are drawn there, not in the form, and saved with ctx.save({key: value, …}) (ctx.problems: what the
// server said, by key); passive kinds have no Run or Uses.
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  const PASSIVE = ["knowledge", "mcp", "workflow"];                    // eki/components PASSIVE: read or attached, never called
  for (const f of ["cardknowledge.js", "cardmcp.js", "cardworkflow.js", "cardsessions.js"]) {
    if (document.querySelector && !document.querySelector(`script[src="/ui/${f}"]`)) {
      const t = document.createElement("script");
      t.src = "/ui/" + f; t.async = false;
      document.head.appendChild(t);
    }
  }
  if (document.querySelector && !document.querySelector('script[src="/ui/choicemenu.js"]')) {
    const t = document.createElement("script");
    t.src = "/ui/choicemenu.js"; t.async = false;
    t.onload = () => redraw();                // a card opened before it arrived is drawn again with it
    document.head.appendChild(t);
  }
  if (document.querySelector && !document.querySelector('link[href="/ui/choicemenu.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/choicemenu.css";
    document.head.appendChild(l);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const P = { el: null, pid: null, card: null, ro: "", ctx: null, last: {}, problems: {}, err: {},
              run: null, uses: null, use: null, useData: null };

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

  // a setting with a fixed set of values (card.choices, declared by its kind) is a dropdown, or the switch
  // when its values are true/false; one the spec lacks shows its card.choice_defaults value
  const choices = () => P.card.choices || {};
  const bools = (cs) => cs.length > 0 && cs.every((c) => typeof c === "boolean");

  function fields() {
    const spec = P.card.spec || {}, defs = P.card.choice_defaults || {};
    const own = (kindPart() && kindPart().keys) || [];      // drawn by the kind's own part
    const keys = Object.keys(spec).filter((k) => k !== "kind" && k !== "pool" && !own.includes(k));
    for (const k of Object.keys(choices())) if (!keys.includes(k) && !own.includes(k) && k in defs) keys.push(k);
    for (const k of Object.keys(fieldHelp())) if (!keys.includes(k) && !own.includes(k)) keys.push(k);
    if (isAgent() && !keys.includes("bare")) keys.push("bare");
    return keys.map((k) => ({ key: k, value: k in spec ? spec[k] : k in defs ? defs[k] : k === "bare" ? false : k in fieldHelp() ? "" : undefined }));
  }

  const fieldHelp = () => P.card.field_help || {};
  const help = (key) => (P.card.choice_help || {})[key] || {};

  // a value outside the choices stays shown on the button, marked, until another is picked
  function choice(f, cs, off) {
    const bad = !cs.some((c) => c === f.value), cm = window.choiceMenu;
    return (cm ? cm.html(f.key, cs, f.value, help(f.key), off) : `<code>${esc(f.value)}</code>`) +
      (bad ? `<div class="pj-err set-bad">not one of: ${esc(cs.join(", "))}</div>` : "");
  }

  function control(f, off) {
    const v = f.value, dis = off ? " disabled" : "", at = `data-key="${esc(f.key)}"${dis}`, cs = choices()[f.key];
    if (Array.isArray(cs) && cs.length && !(bools(cs) && typeof v === "boolean")) return choice(f, cs, off);
    if (typeof v === "boolean") {
      return `<label class="set-check"><input type="checkbox" ${at} data-type="bool"${v ? " checked" : ""}> ${v ? "true" : "false"}</label>`;
    }
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
        <div class="pp-ctl">${control(f, off)}${fieldHelp()[f.key] ? `<div class="dim pp-help">${esc(fieldHelp()[f.key])}</div>` : ""}${P.problems[f.key] ? `<div class="pj-err">${esc(P.problems[f.key])}</div>` : ""}</div></div>`);
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

  function saveSetting(el) {
    const key = el.dataset.key;
    let value;
    try { value = valueOf(el); } catch (x) { P.problems[key] = "not valid JSON: " + x.message; return redrawSettings(); }
    return saveValue(key, value);
  }
  const saveValue = (key, value) => saveSettings({ [key]: value });
  async function saveSettings(set) {
    if (!P.card) return;
    const keys = Object.keys(set);
    for (const k of keys.concat("")) delete P.problems[k];
    delete P.err.settings;
    try {
      const got = await P.ctx.api(url(), { settings: set });
      if (got.card && typeof got.card === "object") P.card = got.card;
    } catch (x) {
      const pr = x.problems || {};
      if (Object.keys(pr).length) Object.assign(P.problems, pr); else P.problems[keys[0]] = x.message;
    }
    redrawSettings();                          // a refused value goes back to the saved one
    drawKind();
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

  // ---- the panel ------------------------------------------------------------------------------

  function head() {
    const c = P.card;
    return `<div class="pj-row"><span class="pc-state">${esc(c.error ? "broken" : c.state || "idle")}</span></div>
      ${c.error ? `<div class="pj-err">${esc(c.error)}</div>` : ""}
      ${(c.kind === "skills" ? [] : c.notes || []).map((n) => `<div class="dim">${esc(n)}</div>`).join("")}
      <div class="dim pp-path"><code>${esc(c.card || c.folder || "")}</code></div>`;
  }

  function frame() {
    const agent = isAgent(), part = kindPart();
    P.framed = part;
    P.el.innerHTML = `<div class="pj-card pp pp-cardview">
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
      ${agent ? '<h4 class="pp-h">Sessions</h4><div class="pp-sessions"></div>' : ""}</div>
      ${agent ? '<div class="pj-card pp pp-sessview" hidden></div>' : ""}`;
    P.last = {};
    if (agent && window.cardSessions) {
      cardSessions.attach(q(".pp-sessions"), q(".pp-sessview"), q(".pp-cardview"),
        { api: P.ctx.api, url, ago, name: P.card.name });
    }
  }

  function drawKind() {
    const part = kindPart(), box = q(".pp-kind");
    if (part && box) part.draw(box, P.card, P.pid, P.ro, { save: saveSettings, problems: P.problems, editable: editable() });
  }

  function draw() {
    if (kindPart() !== P.framed) frame();         // a kind's part registered after the card opened
    put(".pp-head", head());
    if (window.choiceMenu && !P.el.dataset.cmWired) {   // a picked choice is saved like any other setting
      P.el.dataset.cmWired = "1";
      choiceMenu.attach(P.el, (key, value) => saveValue(key, value));
    }
    drawKind();
    const a = document.activeElement;
    const typing = a && P.el.contains(a) && a.matches && a.matches("input, textarea, select, .cm-list");
    if (!(typing && a.closest(".pp-settings"))) put(".pp-settings", settings());
    if (!(typing && a.closest(".pp-prompt"))) put(".pp-prompt", prompt());
    put(".pp-runout", runPart());
    put(".pp-uses", usesPart());
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
    if (t.closest(".pp-detail")) return;
    const u = t.closest("[data-use]");
    if (u) return openUse(u.dataset.use);
  }

  function open(el, pid, card, ro, ctx) {
    close();
    if (!el) return;
    if (card.kind === "instructions" && window.projectInstructions) return projectInstructions.open(el, pid, card, ro, ctx);
    Object.assign(P, { el, pid, card, ro, ctx, problems: {}, err: {}, run: null, uses: null, use: null, useData: null });
    if (!el.dataset.ppWired) {
      el.dataset.ppWired = "1";
      for (const ev of ["click", "change", "focusout", "submit"]) el.addEventListener(ev, onEvent);
    }
    frame();
    draw();
    if (!passive()) loadUses();
  }

  // the cards reloaded: the card as it is now
  function update(card, ro) {
    if (card.kind === "instructions" && window.projectInstructions) return projectInstructions.update(card, ro);
    if (!P.card || !P.el || card.name !== P.card.name) return;
    P.card = card; P.ro = ro;
    draw();
    if (!passive()) loadUses();
    if (isAgent() && window.cardSessions) cardSessions.refresh();
  }

  function close() {
    window.projectInstructions && projectInstructions.close();
    window.cardSessions && cardSessions.close();
    const part = P.card && kindPart();
    if (part && part.close) part.close();
    if (P.run) clearTimeout(P.run.timer);
    if (P.el) P.el.innerHTML = "";
    Object.assign(P, { card: null, run: null, last: {}, framed: null });
  }

  // a kind's part or the sessions arrived: the open card framed and drawn again with it
  function redraw() { if (P.card && P.el) { P.framed = undefined; draw(); } }

  window.projectCardPanel = { open, update, close, redraw };
})();
