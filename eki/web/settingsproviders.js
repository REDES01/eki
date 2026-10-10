// Settings → Providers: providers.json's entries, each folded to one line that opens to its
// editor (one open at a time), the ComfyUI entry eki adds only in memory shown as such, and the
// built-in entries as a group of read-only lines. Registers the "providers"
// section with settings.js; names() is the list every target picker offers.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ABILITIES = ["text", "tools", "web", "vision", "image", "image-edit"];   // providers.ABILITIES
  const KINDS = ["claude_code", "codex", "local", "comfyui"];                    // providers.KINDS, not fake/command
  const MARKS = ["can_effective", "unwritten", "builtin"];                       // what read() adds to an entry
  const effective = () => settings.got("providers").effective.entries;
  let open = null;   // the one provider whose editor shows: page state only, none after a refresh

  // the providers a target may name: the written and in-memory entries, then new ones in the draft
  function names() {
    const got = window.settings && settings.got("providers");
    const eff = got ? got.effective.entries : {};
    const out = Object.keys(eff).filter((n) => !eff[n].builtin);
    for (const n of Object.keys((window.settings && settings.draft("providers")) || {})) if (!out.includes(n)) out.push(n);
    return out;
  }

  // the editor behind a provider's summary line
  function provBody(name, cfg, can, byKind, unwritten) {
    const { check, text, field } = settingsForm;
    const kinds = KINDS.includes(cfg.kind) ? KINDS : KINDS.concat([cfg.kind || ""]);
    const known = ["kind", "label", "about", "can", "off", "base_url", "keep_up", "idle_stop", "models"];
    const other = Object.keys(cfg).filter((k) => !known.includes(k) && !MARKS.includes(k));
    return `<div class="set-top">${text("name", name, "name")}<span class="grow"></span>
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
      ${other.length ? `<div class="dim set-other">Also: ${other.map((k) => `<code>${esc(k)}</code>`).join(", ")} — edit these in Raw JSON.</div>` : ""}`;
  }

  // one provider folded to a line: name, kind · label, what it can do, off, its model list
  function provFold(name, cfg, eff, unwritten) {
    const byKind = !Array.isArray(cfg.can);
    const can = byKind ? (eff && eff.can_effective) || [] : cfg.can;
    const sum = `<b>${esc(name)}</b><span class="dim">${esc(cfg.kind || "")}${cfg.label ? " · " + esc(cfg.label) : ""}</span>
      ${can.map((a) => `<span class="set-pill">${esc(a)}</span>`).join("")}${cfg.off ? '<span class="set-pill">off</span>' : ""}
      ${unwritten ? settingsForm.memo : ""}<span class="grow"></span>
      ${window.settingsModels && !unwritten ? settingsModels.line(name) : ""}`;
    const html = settingsForm.fold(name, sum, open === name ? provBody(name, cfg, can, byKind, unwritten) : "", open === name);
    return unwritten ? html.replace('class="set-fold', 'class="set-fold added') : html;
  }

  function providersForm(d) {
    const { group, line } = settingsForm;
    const eff = effective();
    const mem = Object.keys(eff).filter((n) => eff[n].unwritten && !(n in d));
    const built = Object.keys(eff).filter((n) => eff[n].builtin);
    return `${group("Providers", Object.keys(d).map((n) => provFold(n, d[n] || {}, eff[n], false)).join("")
        + mem.map((n) => provFold(n, eff[n], eff[n], true)).join(""))}
      <button type="button" class="ghost" data-act="addprov">＋ Add a provider</button>
      ${built.length ? group("Built in", built.map((n) => line(`<b>${esc(n)}</b>`, `<span class="dim">${esc(eff[n].kind)}</span>`,
        `${esc(eff[n].label || "")} — always there, not written to providers.json`)).join("")) : ""}`;
  }

  function provOf(card) {
    const d = settings.draft("providers"), name = card.dataset.fold;
    if (!(name in d) && effective()[name]?.unwritten) {
      const cfg = settings.clone(effective()[name]);
      MARKS.forEach((k) => delete cfg[k]);
      d[name] = cfg;
      card.classList.remove("added");
      card.querySelector(".set-mem")?.remove();
    }
    return d[name];
  }

  function editProviders(el, d, card, ev) {
    if (!card || !card.dataset.fold) return false;
    const f = el.dataset.f;
    if (f === "name") {
      if (ev.type !== "change") return false;
      const old = card.dataset.fold, name = el.value.trim();
      if (name === old) return false;
      if (!name || name in d || effective()[name]?.builtin) {
        settings.note("providers", "err", `There is already a provider named ${esc(JSON.stringify(name))}, or the name is empty.`);
        el.value = old;
        return false;
      }
      provOf(card);
      // the draft object is the frame's: rename in place, keeping the order
      const entries = Object.entries(d).map(([k, v]) => [k === old ? name : k, v]);
      Object.keys(d).forEach((k) => delete d[k]);
      Object.assign(d, Object.fromEntries(entries));
      if (open === old) open = name;
      settings.note("providers", "info", `Renamed ${esc(old)} to ${esc(name)}. Routing rows that name ${esc(old)} are not changed.`);
      return true;
    }
    const cfg = provOf(card);
    const put = (k, v) => { if (v === "" || v === false || v == null) delete cfg[k]; else cfg[k] = v; };
    if (el.dataset.can) {
      const can = new Set(cfg.can || []);
      el.checked ? can.add(el.dataset.can) : can.delete(el.dataset.can);
      cfg.can = ABILITIES.filter((a) => can.has(a));
      return true;   // the summary's pills follow
    } else if (f === "bykind") {
      if (el.checked) delete cfg.can;
      else cfg.can = settings.clone((effective()[card.dataset.fold] || {}).can_effective || []);
      return true;
    } else if (f === "off" || f === "keep_up") { put(f, el.checked); return f === "off"; }
    else if (f === "idle_stop") put(f, el.value === "" ? null : +el.value);
    else if (f === "kind") { cfg.kind = el.value; return true; }
    else if (f) put(f, el.value);
    return false;
  }

  function actProviders(act, btn, card) {
    const d = settings.draft("providers");
    if (act === "addprov") {
      let n = 1; while (("provider-" + n) in d) n++;
      d["provider-" + n] = { kind: "local", label: "" };
      open = "provider-" + n;
      return true;
    }
    if (!card || !card.dataset.fold) return false;
    const name = card.dataset.fold;
    if (act === "fold") { open = open === name ? null : name; return true; }
    if (act === "delprov" && confirm(`Delete ${name}?`)) {
      delete d[name];
      if (open === name) open = null;
      return true;
    }
    return false;
  }

  window.settingsProviders = { names };
  const providers = { title: "Providers", file: "providers", html: providersForm, edit: editProviders, act: actProviders };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("providers", providers);
  else (window.settingsSections = window.settingsSections || []).push(["providers", providers]);
})();
