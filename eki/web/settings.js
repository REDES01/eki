// Settings: routing.json and providers.json, as a form or as raw JSON, over one draft per file.
// It takes #main's place while the address is #settings (nav.js). The page decides nothing:
// the server checks the whole file before it writes (eki/api_settings.py), keeps the old one
// as <name>.json.bak, and refuses (409) a file that changed on disk since it was loaded.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ABILITIES = ["text", "tools", "web", "vision", "image", "image-edit"];   // providers.ABILITIES
  const KINDS = ["claude_code", "codex", "local", "comfyui"];                    // providers.KINDS, not fake/command
  const MODES = ["propose", "apply"];                                            // queue.MODES
  const MARKS = ["can_effective", "unwritten", "builtin"];                       // what read() adds to an entry
  const NAMES = { routing: "Routing", providers: "Providers" };
  const S = { on: false, tab: "routing", mode: "form", got: {}, draft: {}, raw: {}, rawErr: {}, note: {} };
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const memo = '<span class="set-mem" title="eki adds this in memory; it is written only if you edit it">not saved — in memory</span>';

  function box() {
    let el = $("setview");
    if (!el) {
      el = document.createElement("div");
      el.id = "setview";
      el.innerHTML = `<div class="set-bar">
          <div class="set-seg" id="set-tabs">${Object.keys(NAMES).map((n) => `<button type="button" data-tab="${n}">${NAMES[n]}</button>`).join("")}</div>
          <div class="set-seg" id="set-modes"><button type="button" data-mode="form">Form</button><button type="button" data-mode="raw">Raw JSON</button></div>
          <span class="grow"></span>
          <button type="button" class="ghost" data-act="reset" title="Throw away the draft and read the file again">Reset to what's on disk</button>
          <button type="button" class="set-save" data-act="save">Save</button>
        </div>
        <div id="set-note"></div><div id="set-body"></div>`;
      $("main").insertBefore(el, $("composer"));
      el.addEventListener("click", onClick);
      el.addEventListener("input", onEdit);
      el.addEventListener("change", onEdit);
    }
    return el;
  }

  async function load(name) {
    const r = await fetch("/api/settings/" + name);
    const got = await r.json();
    if (!r.ok) throw new Error(got.error || r.statusText);
    [S.got[name], S.draft[name], S.raw[name], S.rawErr[name]] = [got, clone(got.data), null, null];
  }

  const dirty = (name) => S.got[name] && JSON.stringify(S.draft[name]) !== JSON.stringify(S.got[name].data);

  function meta() { const g = S.got[S.tab]; if (S.on && g) $("meta").textContent = g.path + (dirty(S.tab) ? " · changed, not saved" : ""); }

  function note(name, kind, html) { S.note[name] = html ? { kind, html } : null; if (name === S.tab) showNote(); }

  function showNote() { const n = S.note[S.tab]; $("set-note").innerHTML = n ? `<div class="set-msg ${n.kind}">${n.html}</div>` : ""; }

  // ---- the routing form --------------------------------------------------------------------

  function providerNames() {
    const eff = S.got.providers ? S.got.providers.effective.entries : {};
    const names = Object.keys(eff).filter((n) => !eff[n].builtin);
    for (const n of Object.keys(S.draft.providers || {})) if (!names.includes(n)) names.push(n);
    return names;
  }

  function addedRows() {
    const have = new Set((S.draft.routing.rows || []).map((r) => r && r.key));
    return (S.got.routing.effective.rows || []).filter((r) => r.added && !have.has(r.key));
  }

  const check = (on, attrs, label, off) =>
    `<label class="set-check${off ? " off" : ""}"><input type="checkbox" ${attrs}${on ? " checked" : ""}${off ? " disabled" : ""}> ${esc(label)}</label>`;
  const text = (f, v, ph) => `<input class="set-in" data-f="${f}" value="${esc(v)}" placeholder="${esc(ph || "")}" spellcheck="false">`;
  const field = (label, html) => `<label class="set-field"><span>${label}</span>${html}</label>`;

  function rowCard(r, id, added) {
    const targets = Array.isArray(r.targets) ? r.targets : [];
    const needs = Array.isArray(r.needs) ? r.needs : [];
    const more = providerNames().filter((n) => !targets.includes(n));
    const last = targets.length - 1;
    return `<div class="set-card${added ? " added" : ""}" data-row="${esc(id)}">
      <div class="set-top">${text("key", r.key, "key")}${added ? memo : ""}<span class="grow"></span>
        ${added ? "" : '<button type="button" class="ghost small" data-act="delrow" title="Delete this row">Delete</button>'}</div>
      ${field("Title", text("title", r.title, "what this row is for"))}
      ${field("Needs", `<div class="set-checks">${ABILITIES.map((a) => check(needs.includes(a), `data-need="${a}"`, a)).join("")}</div>`)}
      ${field("Examples <i class=\"dim\">one per line</i>", `<textarea class="set-in" data-f="examples" rows="${Math.max(2, (r.examples || []).length)}">${esc((r.examples || []).join("\n"))}</textarea>`)}
      ${field("Targets <i class=\"dim\">first that can answer wins</i>", `<ol class="set-targets">${targets.map((t, i) => `<li><b>${esc(t)}</b><span class="grow"></span>
        <button type="button" class="ghost small" data-act="up" data-t="${i}" ${i ? "" : "disabled"} title="Earlier">↑</button>
        <button type="button" class="ghost small" data-act="down" data-t="${i}" ${i < last ? "" : "disabled"} title="Later">↓</button>
        <button type="button" class="ghost small" data-act="rmt" data-t="${i}" title="Remove">✕</button></li>`).join("")}</ol>
        <select class="set-in" data-f="addt"><option value="">＋ add a provider…</option>${more.map((n) => `<option>${esc(n)}</option>`).join("")}</select>`)}
    </div>`;
  }

  function routingForm() {
    const d = S.draft.routing;
    const me = d.self && typeof d.self === "object" ? d.self : {};
    const pl = me.planner && typeof me.planner === "object" ? me.planner : {};
    const names = providerNames();
    const opt = (v, cur, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label || v)}</option>`;
    return `<section class="set-sect"><h3>Routing</h3>
        ${check(d.checker_wakes !== false, 'data-f="checker_wakes"', "Wake an on-demand checker model for the prompt check")}</section>
      <section class="set-sect"><h3>eki self</h3><div class="set-grid" data-self>
        ${field("Autonomy", `<select class="set-in" data-f="autonomy">${MODES.map((m) => opt(m, me.autonomy || "propose")).join("")}</select>`)}
        ${field("Parallel", `<input class="set-in" type="number" min="1" data-f="parallel" value="${esc(me.parallel == null ? "" : me.parallel)}" placeholder="3">`)}
        ${field("Planner", `<select class="set-in" data-f="planner.provider">${opt("", pl.provider || "", "code row's first")}${names.map((n) => opt(n, pl.provider || "")).join("")}</select>`)}
        ${field("Planner model", text("planner.model", pl.model, "provider's default"))}
      </div></section>${window.settingsNotify ? settingsNotify.form(d) : ""}
      <section class="set-sect"><h3>Rows</h3>
        ${(d.rows || []).map((r, i) => rowCard(r || {}, "d" + i, false)).join("")}
        ${addedRows().map((r) => rowCard(r, "a:" + r.key, true)).join("")}
        <button type="button" class="ghost" data-act="addrow">＋ Add a row</button></section>`;
  }

  // the draft row behind a card; an in-memory row joins the draft when it is first edited
  function rowOf(card) {
    const d = S.draft.routing, id = card.dataset.row;
    if (!Array.isArray(d.rows)) d.rows = [];
    if (id[0] === "d") return d.rows[+id.slice(1)];
    const r = clone(addedRows().find((x) => x.key === id.slice(2)));
    delete r.added;
    d.rows.push(r);
    card.dataset.row = "d" + (d.rows.length - 1);
    card.classList.remove("added");
    card.querySelector(".set-mem")?.remove();
    return r;
  }

  function editRouting(el, card) {
    const d = S.draft.routing, f = el.dataset.f;
    if (el.closest("[data-notify]")) return settingsNotify.edit(el, d);   // settingsnotify.js
    if (el.closest("[data-self]")) {
      if (!d.self || typeof d.self !== "object") d.self = {};
      if (f === "autonomy") d.self.autonomy = el.value;
      else if (f === "parallel") { if (el.value === "") delete d.self.parallel; else d.self.parallel = +el.value; }
      else {
        const pl = d.self.planner && typeof d.self.planner === "object" ? d.self.planner : (d.self.planner = {});
        const k = f.split(".")[1];
        if (el.value.trim()) pl[k] = el.value.trim(); else delete pl[k];
        if (!Object.keys(pl).length) delete d.self.planner;
      }
      return false;
    }
    if (f === "checker_wakes") { d.checker_wakes = el.checked; return false; }
    if (!card) return false;
    const r = rowOf(card);
    if (el.dataset.need) {
      const needs = new Set(Array.isArray(r.needs) ? r.needs : []);
      el.checked ? needs.add(el.dataset.need) : needs.delete(el.dataset.need);
      r.needs = ABILITIES.filter((a) => needs.has(a)).concat([...needs].filter((a) => !ABILITIES.includes(a)));
    } else if (f === "examples") r.examples = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (f === "addt") {
      if (!el.value) return false;
      r.targets = (Array.isArray(r.targets) ? r.targets : []).concat([el.value]);
      return true;
    } else if (f === "key" || f === "title") r[f] = el.value;
    return false;
  }

  function actRouting(act, btn, card) {
    const d = S.draft.routing;
    if (act === "addrow") {
      (Array.isArray(d.rows) ? d.rows : (d.rows = [])).push({ key: "", title: "", examples: [], needs: ["text"], targets: [] });
      return true;
    }
    if (!card) return false;
    if (act === "delrow") {
      const i = +card.dataset.row.slice(1);
      if (!confirm(`Delete the row ${JSON.stringify(d.rows[i].key || "")}? (Only in the draft until you save.)`)) return false;
      d.rows.splice(i, 1);
      return true;
    }
    const r = rowOf(card);
    const t = Array.isArray(r.targets) ? r.targets : (r.targets = []);
    const i = +btn.dataset.t;
    if (act === "rmt") t.splice(i, 1);
    else if (act === "up" && i > 0) [t[i - 1], t[i]] = [t[i], t[i - 1]];
    else if (act === "down" && i < t.length - 1) [t[i + 1], t[i]] = [t[i], t[i + 1]];
    return true;
  }

  // ---- the providers form ------------------------------------------------------------------

  function provCard(name, cfg, eff, unwritten) {
    const kinds = KINDS.includes(cfg.kind) ? KINDS : KINDS.concat([cfg.kind || ""]);
    const byKind = !Array.isArray(cfg.can);
    const can = byKind ? (eff && eff.can_effective) || [] : cfg.can;
    const known = ["kind", "label", "about", "can", "off", "base_url", "keep_up", "idle_stop"];
    const other = Object.keys(cfg).filter((k) => !known.includes(k) && !MARKS.includes(k));
    return `<div class="set-card${unwritten ? " added" : ""}" data-prov="${esc(name)}"${unwritten ? " data-unw" : ""}>
      <div class="set-top">${text("name", name, "name")}${unwritten ? memo : ""}<span class="grow"></span>
        ${check(cfg.off, 'data-f="off"', "off")}
        ${unwritten ? "" : '<button type="button" class="ghost small" data-act="delprov" title="Delete this entry">Delete</button>'}</div>
      <div class="set-grid">
        ${field("Kind", `<select class="set-in" data-f="kind">${kinds.map((k) => `<option${k === cfg.kind ? " selected" : ""}>${esc(k)}</option>`).join("")}</select>`)}
        ${field("Label", text("label", cfg.label))}
        ${field("Base URL", text("base_url", cfg.base_url, "—"))}
        ${field("Idle stop <i class=\"dim\">minutes</i>", `<input class="set-in" type="number" min="0" data-f="idle_stop" value="${esc(cfg.idle_stop == null ? "" : cfg.idle_stop)}" placeholder="—">`)}
      </div>
      ${field("About", text("about", cfg.about, "what it's good for"))}
      ${field("Can", `<div class="set-checks">${check(byKind, 'data-f="bykind"', "kind default")}
        ${ABILITIES.map((a) => check(can.includes(a), `data-can="${a}"`, a, byKind)).join("")}</div>`)}
      ${check(cfg.keep_up, 'data-f="keep_up"', "keep up (start it and keep it running while there's room)")}
      ${other.length ? `<div class="dim set-other">Also: ${other.map((k) => `<code>${esc(k)}</code>`).join(", ")} — edit these in Raw JSON.</div>` : ""}
    </div>`;
  }

  function providersForm() {
    const d = S.draft.providers;
    const eff = S.got.providers.effective.entries;
    const mem = Object.keys(eff).filter((n) => eff[n].unwritten && !(n in d));
    const built = Object.keys(eff).filter((n) => eff[n].builtin);
    return `<section class="set-sect"><h3>Providers</h3>
      ${Object.keys(d).map((n) => provCard(n, d[n] || {}, eff[n], false)).join("")}
      ${mem.map((n) => provCard(n, eff[n], eff[n], true)).join("")}
      <button type="button" class="ghost" data-act="addprov">＋ Add a provider</button></section>
      ${built.length ? `<section class="set-sect"><h3>Built in</h3>${built.map((n) => `<div class="set-built"><b>${esc(n)}</b>
        <span class="dim">${esc(eff[n].kind)} · ${esc(eff[n].label || "")} — always there, not written to providers.json</span></div>`).join("")}</section>` : ""}`;
  }

  function provOf(card) {
    const d = S.draft.providers;
    const name = card.dataset.prov;
    if (card.hasAttribute("data-unw")) {
      const cfg = clone(S.got.providers.effective.entries[name]);
      MARKS.forEach((k) => delete cfg[k]);
      d[name] = cfg;
      card.removeAttribute("data-unw");
      card.classList.remove("added");
      card.querySelector(".set-mem")?.remove();
    }
    return d[name];
  }

  function editProviders(el, card, ev) {
    if (!card) return false;
    const f = el.dataset.f;
    if (f === "name") {
      if (ev.type !== "change") return false;
      const old = card.dataset.prov, name = el.value.trim();
      if (name === old) return false;
      const eff = S.got.providers.effective.entries;
      if (!name || name in S.draft.providers || eff[name]?.builtin) {
        note("providers", "err", `There is already a provider named ${esc(JSON.stringify(name))}, or the name is empty.`);
        el.value = old;
        return false;
      }
      provOf(card);
      S.draft.providers = Object.fromEntries(Object.entries(S.draft.providers).map(([k, v]) => [k === old ? name : k, v]));
      note("providers", "info", `Renamed ${esc(old)} to ${esc(name)}. Routing rows that name ${esc(old)} are not changed.`);
      return true;
    }
    const cfg = provOf(card);
    const put = (k, v) => { if (v === "" || v === false || v == null) delete cfg[k]; else cfg[k] = v; };
    if (el.dataset.can) {
      const can = new Set(cfg.can || []);
      el.checked ? can.add(el.dataset.can) : can.delete(el.dataset.can);
      cfg.can = ABILITIES.filter((a) => can.has(a));
    } else if (f === "bykind") {
      if (el.checked) delete cfg.can;
      else cfg.can = clone((S.got.providers.effective.entries[card.dataset.prov] || {}).can_effective || []);
      return true;
    } else if (f === "off" || f === "keep_up") put(f, el.checked);
    else if (f === "idle_stop") put(f, el.value === "" ? null : +el.value);
    else if (f === "kind") cfg.kind = el.value;
    else if (f) put(f, el.value);
    return false;
  }

  function actProviders(act, btn, card) {
    const d = S.draft.providers;
    if (act === "addprov") {
      let n = 1; while (("provider-" + n) in d) n++;
      d["provider-" + n] = { kind: "local", label: "" };
      return true;
    }
    if (act === "delprov" && card && confirm(`Delete ${card.dataset.prov}? (Only in the draft until you save.)`)) {
      delete d[card.dataset.prov];
      return true;
    }
    return false;
  }

  // ---- the frame ---------------------------------------------------------------------------

  function render() {
    if (!S.on) return;
    box().querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === S.tab));
    box().querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
    showNote();
    const name = S.tab;
    if (!S.got[name] || (name === "routing" && !S.got.providers)) { $("set-body").innerHTML = '<div class="dim pad">Loading…</div>'; return; }
    if (S.mode === "raw") {
      const txt = S.raw[name] != null ? S.raw[name] : JSON.stringify(S.draft[name], null, 2);
      $("set-body").innerHTML = `<div id="set-rawerr" class="err"></div><textarea id="set-raw" spellcheck="false">${esc(txt)}</textarea>`;
      rawErr();
    } else {
      const top = $("set-body").scrollTop;
      $("set-body").innerHTML = name === "routing" ? routingForm() : providersForm();
      $("set-body").scrollTop = top;
    }
    meta();
  }

  function rawErr() {
    const e = $("set-rawerr"), m = S.rawErr[S.tab];
    if (e) e.textContent = m ? `Not valid JSON yet — ${m}. Nothing is sent until it parses.` : "";
  }

  function onEdit(ev) {
    const el = ev.target;
    if (el.id === "set-raw") {
      S.raw[S.tab] = el.value;
      try { S.draft[S.tab] = JSON.parse(el.value); S.rawErr[S.tab] = null; } catch (e) { S.rawErr[S.tab] = e.message; }
      rawErr();
      return meta();
    }
    if (!el.matches("input, select, textarea")) return;
    if (ev.type === "input" && (el.type === "checkbox" || el.tagName === "SELECT")) return;   // their change event does it
    const card = el.closest(".set-card");
    const again = S.tab === "routing" ? editRouting(el, card) : editProviders(el, card, ev);
    if (again) render(); else meta();
  }

  async function onClick(ev) {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.tab) { S.tab = b.dataset.tab; return render(); }
    if (b.dataset.mode) {
      const bad = Object.keys(NAMES).find((n) => S.rawErr[n]);
      if (b.dataset.mode === "form" && bad) return note(S.tab, "err", `Fix the ${NAMES[bad]} JSON first: the form shows the draft, and it doesn't parse.`);
      S.mode = b.dataset.mode;
      S.raw = {};
      return render();
    }
    const act = b.dataset.act;
    if (act === "save") return save();
    if (act === "reset" || act === "reload") {
      if (act === "reset" && dirty(S.tab) && !confirm("Throw away the changes in this draft?")) return;
      return reload(S.tab, "Back to what's on disk.");
    }
    const card = b.closest(".set-card");
    const again = S.tab === "routing" ? actRouting(act, b, card) : actProviders(act, b, card);
    if (again) render();
  }

  async function reload(name, said) {
    try {
      await load(name);
      note(name, "info", esc(said || ""));
    } catch (e) {
      note(name, "err", "Can't read the settings: " + esc(e.message));
    }
    render();
  }

  async function save() {
    const name = S.tab;
    if (S.rawErr[name]) return note(name, "err", "The JSON doesn't parse yet — nothing was sent.");
    const btn = box().querySelector(".set-save");
    btn.disabled = true;
    try {
      const r = await fetch("/api/settings/" + name, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
        body: JSON.stringify({ data: S.draft[name], mtime: S.got[name].mtime }),
      });
      const got = await r.json().catch(() => ({}));
      if (r.status === 409) {
        note(name, "warn", `${esc(got.error || "The file changed on disk since it was loaded.")} Your draft is kept here.
          <button type="button" class="ghost small" data-act="reload">Reload from disk</button>`);
      } else if (r.status === 400 && Array.isArray(got.problems)) {
        note(name, "err", `Not saved — ${got.problems.length} problem${got.problems.length === 1 ? "" : "s"}:<ul>${got.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`);
      } else if (!r.ok) {
        note(name, "err", "Not saved: " + esc(got.error || r.statusText));
      } else {
        await reload(name, "");
        note(name, "ok", `Saved ${esc(got.path)}. The old file is kept as <code>${esc(got.backup)}</code>. The next request is routed with it.`);
      }
    } catch (e) {
      note(name, "err", "Not saved: " + esc(e.message));
    } finally {
      btn.disabled = false;
    }
  }

  async function show() {
    [S.on, S.got, box().hidden] = [true, {}, false];
    [$("title").textContent, $("meta").textContent] = ["Settings", ""];
    render();
    try {
      await Promise.all(Object.keys(NAMES).map(load));
    } catch (e) {
      note(S.tab, "err", "Can't read the settings: " + esc(e.message));
    }
    render();
  }

  function hide() { if (S.on) { S.on = false; box().hidden = true; } }

  window.settings = { show, hide, on: () => S.on };
  nav.register("settings", { show, hide });
})();
