// Settings → Providers: providers.json's entries, one card each, the ComfyUI entry eki adds only
// in memory shown as such, and the built-in entries as a plain list. Registers the "providers"
// section with settings.js; names() is the list every target picker offers.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ABILITIES = ["text", "tools", "web", "vision", "image", "image-edit"];   // providers.ABILITIES
  const KINDS = ["claude_code", "codex", "local", "comfyui"];                    // providers.KINDS, not fake/command
  const MARKS = ["can_effective", "unwritten", "builtin"];                       // what read() adds to an entry
  const effective = () => settings.got("providers").effective.entries;

  // the providers a target may name: the written and in-memory entries, then new ones in the draft
  function names() {
    const got = window.settings && settings.got("providers");
    const eff = got ? got.effective.entries : {};
    const out = Object.keys(eff).filter((n) => !eff[n].builtin);
    for (const n of Object.keys((window.settings && settings.draft("providers")) || {})) if (!out.includes(n)) out.push(n);
    return out;
  }

  function provCard(name, cfg, eff, unwritten) {
    const { check, text, field, memo } = settingsForm;
    const kinds = KINDS.includes(cfg.kind) ? KINDS : KINDS.concat([cfg.kind || ""]);
    const byKind = !Array.isArray(cfg.can);
    const can = byKind ? (eff && eff.can_effective) || [] : cfg.can;
    const known = ["kind", "label", "about", "can", "off", "base_url", "keep_up", "idle_stop", "models"];
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
      ${window.settingsModels && !unwritten ? settingsModels.line(name) : ""}
    </div>`;
  }

  function providersForm(d) {
    const eff = effective();
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
    const d = settings.draft("providers"), name = card.dataset.prov;
    if (card.hasAttribute("data-unw")) {
      const cfg = settings.clone(effective()[name]);
      MARKS.forEach((k) => delete cfg[k]);
      d[name] = cfg;
      card.removeAttribute("data-unw");
      card.classList.remove("added");
      card.querySelector(".set-mem")?.remove();
    }
    return d[name];
  }

  function editProviders(el, d, card, ev) {
    if (!card || !card.dataset.prov) return false;
    const f = el.dataset.f;
    if (f === "name") {
      if (ev.type !== "change") return false;
      const old = card.dataset.prov, name = el.value.trim();
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
      settings.note("providers", "info", `Renamed ${esc(old)} to ${esc(name)}. Routing rows that name ${esc(old)} are not changed.`);
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
      else cfg.can = settings.clone((effective()[card.dataset.prov] || {}).can_effective || []);
      return true;
    } else if (f === "off" || f === "keep_up") put(f, el.checked);
    else if (f === "idle_stop") put(f, el.value === "" ? null : +el.value);
    else if (f === "kind") cfg.kind = el.value;
    else if (f) put(f, el.value);
    return false;
  }

  function actProviders(act, btn, card) {
    const d = settings.draft("providers");
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

  window.settingsProviders = { names };
  const providers = { title: "Providers", file: "providers", html: providersForm, edit: editProviders, act: actProviders };
  if (window.settings) settings.section("providers", providers);
  else (window.settingsSections = window.settingsSections || []).push(["providers", providers]);
})();
