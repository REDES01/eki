// Settings: one picker for a target — provider, then model, then effort — over each provider's
// read list from GET /api/models (eki/api_models.py). settings.js uses it for every row target
// and settingsmodels.js for each role. html() draws it inside a [data-tgt] wrapper; value() reads
// the wrapper back as targets.fmt()'s shortest form: "claude", "claude:opus", or
// {"provider", "model"?, "effort"?} when an effort is set. A saved model or effort the program no
// longer offers is shown in amber (.set-amber) with choose()'s words (eki/targets.py). label()
// is targets.label(): one line, "claude · Opus 5.5 · high".
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const TIERS = ["fast", "default", "top"];                                     // targets.TIERS
  const amber = (s) => (s ? `<div class="dim set-amber">${esc(s)}</div>` : "");
  const opt = (v, cur, label, more) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}${more || ""}>${esc(label)}</option>`;

  // targets.parse, without the errors: {provider, model, effort}; a tier is kept as the model
  function parse(x) {
    if (x && typeof x === "object") {
      const s = (v) => (typeof v === "string" ? v.trim() : "");
      return { provider: s(x.provider), model: s(x.model), effort: s(x.effort) };
    }
    const t = String(x || "").trim(), i = t.indexOf(":");
    return i < 0 ? { provider: t, model: "", effort: "" } : { provider: t.slice(0, i).trim(), model: t.slice(i + 1).trim(), effort: "" };
  }

  // targets.fmt: text without an effort, an object with only the set keys with one
  function fmt(provider, model, effort) {
    if (!effort) return model ? `${provider}:${model}` : provider;
    return Object.assign({ provider }, model ? { model } : {}, { effort });
  }

  // targets.key: one line, for showing and comparing
  function key(x) {
    if (!x) return "";
    const t = parse(x), out = t.model ? `${t.provider}:${t.model}` : t.provider;
    return t.effort ? `${out}@${t.effort}` : out;
  }

  const rowOf = (listed, model) => (listed || []).find((m) => (model ? m.model === model : m.is_default)) || null;

  // the model select, or a free text box when there's no list (and the kind would take one)
  function modelPart(t, p) {
    const listed = p && p.listed;
    if (!listed) {
      if (p && !p.lists && !t.model) return "";
      return `<input class="set-in" data-tm value="${esc(t.model)}" placeholder="its default" spellcheck="false" title="model id">`;
    }
    const efforts = (m) => ` data-efforts="${esc((m.efforts || []).join(","))}" title="${esc(m.description || "")}"`;
    const def = rowOf(listed, "");
    const list = [opt("", t.model, "its default", def ? efforts(def) : "")];
    listed.filter((m) => m.floating).forEach((m) => list.push(opt(m.model, t.model, `${m.label || m.model} — follows the newest`, efforts(m))));
    listed.filter((m) => !m.floating).forEach((m) => list.push(opt(m.model, t.model, m.label && m.label !== m.model ? `${m.model} — ${m.label}` : m.model, efforts(m))));
    if (t.model && !rowOf(listed, t.model))
      list.push(opt(t.model, t.model, TIERS.includes(t.model) ? `${t.model} — tier from providers.json` : `${t.model} (not in the list)`));
    return `<select class="set-in" data-tm>${list.join("")}</select>`;
  }

  // the effort select: only for a model that offers levels, or to show one saved that it doesn't
  function effortPart(t, p) {
    const listed = p && p.listed;
    if (!listed) return p && p.lists && t.effort ? `<input class="set-in" data-te value="${esc(t.effort)}" placeholder="its default" spellcheck="false" title="effort">` : "";
    const row = TIERS.includes(t.model) && !rowOf(listed, t.model) ? null : rowOf(listed, t.model);
    const levels = (row && row.efforts) || [];
    if (!levels.length && !t.effort) return "";
    const list = [opt("", t.effort, "its default")].concat(levels.map((e) => opt(e, t.effort, e === row.default_effort ? `${e} (default)` : e)));
    if (t.effort && !levels.includes(t.effort)) list.push(opt(t.effort, t.effort, `${t.effort} (not offered)`));
    return `<select class="set-in" data-te title="effort">${list.join("")}</select>`;
  }

  // choose()'s notes for what is saved, when the list was read
  function notes(t, p) {
    const listed = p && p.listed;
    if (!listed || !t.provider) return [];
    if (TIERS.includes(t.model) && !rowOf(listed, t.model)) return [];
    if (t.model && !rowOf(listed, t.model)) return [`${t.model} isn't in ${t.provider}'s list any more; its default`];
    const row = rowOf(listed, t.model);
    if (!t.effort || !row) return [];
    const name = t.model || row.model;
    if (!row.efforts) return [`${name} takes no effort`];
    return row.efforts.includes(t.effort) ? [] : [`${t.provider} ${name} doesn't offer effort ${t.effort} any more; its default`];
  }

  // cur: the saved target (text or object; "" or "off" for a role). o.names: the providers to
  // offer; o.models: /api/models' providers; o.extra: [value, label] put first; o.attrs: the
  // wrapper's attributes.
  function html(cur, o) {
    const t = cur === "off" || !cur ? { provider: cur || "", model: "", effort: "" } : parse(cur);
    const models = o.models || {}, names = (o.names || []).slice();
    if (t.provider && t.provider !== "off" && !names.includes(t.provider)) names.push(t.provider);
    const provs = (o.extra || []).concat(names.map((n) => [n, n]));
    const p = t.provider && t.provider !== "off" ? models[t.provider] : null;
    const chosen = p && rowOf(p.listed, t.model);
    const about = chosen && chosen.description ? `<div class="dim">${esc(chosen.description)}</div>` : "";
    return `<span class="set-target" data-tgt ${o.attrs || ""}>
      <select class="set-in" data-tp title="provider">${provs.map(([v, l]) => opt(v, t.provider, l)).join("")}</select>
      ${p || (t.provider && t.provider !== "off" && o.models) ? modelPart(t, p) + effortPart(t, p) : ""}
      ${about}${notes(t, p).map(amber).join("")}</span>`;
  }

  // the wrapper's target after `el` changed: a new provider starts at its defaults, and a new
  // model keeps the effort only when it offers it. "" and "off" come back as they are.
  function value(wrap, el) {
    const get = (a) => { const x = wrap.querySelector(`[${a}]`); return x ? x.value.trim() : ""; };
    const provider = get("data-tp");
    if (!provider || provider === "off" || el.hasAttribute("data-tp")) return provider;
    const model = get("data-tm");
    let effort = get("data-te");
    if (el.hasAttribute("data-tm") && el.tagName === "SELECT" && effort) {
      const o = el.selectedOptions[0], levels = o && o.dataset.efforts ? o.dataset.efforts.split(",") : [];
      if (!levels.includes(effort)) effort = "";
    }
    return fmt(provider, model, effort);
  }

  // targets.label: the provider, the listed model's label (else its id, else "its default"), the effort.
  // models: /api/models' providers (or null); a model is looked up like targets._row.
  function label(cur, models) {
    const t = parse(cur);
    const p = models && models[t.provider];
    const row = rowOf(p && p.listed, t.model);
    const parts = [t.provider, (row && (row.label || row.model)) || t.model || "its default"];
    if (t.effort) parts.push(t.effort);
    return parts.join(" · ");
  }

  window.settingsTarget = { html, value, parse, fmt, key, notes, label };
})();
