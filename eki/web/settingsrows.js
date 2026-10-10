// Settings → Rows: routing.json's rows, each folded to one line (key, title, its targets as
// targets.label() joined by " → "); a click opens that row's editor and closes any other, every
// target one picker (settingstarget.js). Which row is open is page state only (`open`), never in
// the address. Rows eki adds only in memory (table.rows()) are shown as such and join the draft
// when first edited. Registers the "rows" section with settings.js.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ABILITIES = ["text", "tools", "web", "vision", "image", "image-edit"];   // providers.ABILITIES
  let open = null;   // the one open row: "d<i>" (the draft's rows[i]) or "a:<key>" (in memory)

  function addedRows() {
    const have = new Set((settings.draft("routing").rows || []).map((r) => r && r.key));
    return (settings.got("routing").effective.rows || []).filter((r) => r.added && !have.has(r.key));
  }

  const models = () => (window.settingsModels ? settingsModels.models() : null);

  function summary(r, added) {
    const targets = Array.isArray(r.targets) ? r.targets : [];
    const chain = targets.map((t) => settingsTarget.label(t, models())).join(" → ");
    return `<b data-sum="key">${esc(r.key || "(no key)")}</b><span class="dim" data-sum="title">${esc(r.title || "")}</span>
      ${added ? settingsForm.memo : ""}<span class="grow"></span>
      <span class="dim set-chain">${chain ? esc(chain) : "no targets"}</span>`;
  }

  function editor(r, added) {
    const { check, text, line, lines, block, danger } = settingsForm;
    const [targets, needs] = [r.targets, r.needs].map((x) => (Array.isArray(x) ? x : []));
    const names = settingsProviders.names(), last = targets.length - 1;
    const list = `<ol class="set-targets">${targets.map((t, i) => `<li>${settingsTarget.html(t, { names, models: models(), attrs: `data-t="${i}"` })}<span class="grow"></span>
        <button type="button" class="ghost small" data-act="up" data-t="${i}" ${i ? "" : "disabled"} title="Earlier">↑</button>
        <button type="button" class="ghost small" data-act="down" data-t="${i}" ${i < last ? "" : "disabled"} title="Later">↓</button>
        <button type="button" class="ghost small" data-act="rmt" data-t="${i}" title="Remove">✕</button></li>`).join("")}</ol>
      <select class="set-in" data-f="addt"><option value="">＋ add a target…</option>${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select>`;
    return lines("", line("Key", text("key", r.key, "key"), "the name a request is labelled with")
        + line("Title", text("title", r.title, "title"), "what this row is for"))
      + lines("", line("Needs", `<div class="set-checks">${ABILITIES.map((a) => check(needs.includes(a), `data-need="${a}"`, a)).join("")}</div>`,
        "what a target must be able to do"))
      + block("Examples", `<textarea class="set-in" data-f="examples" rows="${Math.max(2, (r.examples || []).length)}">${esc((r.examples || []).join("\n"))}</textarea>`,
        "one per line — requests that belong here")
      + block("Targets", list, "first that can answer wins")
      + (added ? "" : danger("Delete this row", "delrow"));
  }

  function rowLine(r, id, added) {
    const html = settingsForm.fold(id, summary(r, added), id === open ? editor(r, added) : "", id === open);
    return added ? html.replace('class="set-fold', 'class="set-fold added') : html;   // dashed: only in memory
  }

  function form(d) {
    const rows = (d.rows || []).map((r, i) => rowLine(r || {}, "d" + i, false))
      .concat(addedRows().map((r) => rowLine(r, "a:" + r.key, true)));
    return `${settingsForm.group("Rows", rows.join("") || '<div class="set-line dim">No rows yet.</div>')}
      <button type="button" class="ghost" data-act="addrow">＋ Add a row</button>`;
  }

  // the draft row behind a fold; an in-memory row joins the draft when it is first edited
  function rowOf(card) {
    const d = settings.draft("routing"), id = card.dataset.fold;
    if (!Array.isArray(d.rows)) d.rows = [];
    if (id[0] === "d") return d.rows[+id.slice(1)];
    const r = settings.clone(addedRows().find((x) => x.key === id.slice(2)));
    delete r.added;
    d.rows.push(r);
    card.dataset.fold = "d" + (d.rows.length - 1);
    if (open === id) open = card.dataset.fold;
    card.classList.remove("added");
    card.querySelector(".set-mem")?.remove();
    return r;
  }

  function edit(el, d, card) {
    const f = el.dataset.f;
    if (!card || !card.dataset.fold) return false;
    const r = rowOf(card), w = el.closest("[data-tgt]");
    if (w) { r.targets[+w.dataset.t] = settingsTarget.value(w, el); return el.tagName === "SELECT"; }   // settingstarget.js
    if (el.dataset.need) {
      const needs = new Set(Array.isArray(r.needs) ? r.needs : []);
      el.checked ? needs.add(el.dataset.need) : needs.delete(el.dataset.need);
      r.needs = ABILITIES.filter((a) => needs.has(a)).concat([...needs].filter((a) => !ABILITIES.includes(a)));
    } else if (f === "examples") r.examples = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (f === "addt") {
      if (!el.value) return false;
      r.targets = (Array.isArray(r.targets) ? r.targets : []).concat([el.value]);
      return true;
    } else if (f === "key" || f === "title") {
      r[f] = el.value;
      const s = card.querySelector(`[data-sum="${f}"]`);   // the summary follows without a redraw (keeps the caret)
      if (s) s.textContent = f === "key" ? el.value || "(no key)" : el.value;
    }
    return false;
  }

  function act(a, btn, card) {
    const d = settings.draft("routing");
    if (a === "addrow") {
      (Array.isArray(d.rows) ? d.rows : (d.rows = [])).push({ key: "", title: "", examples: [], needs: ["text"], targets: [] });
      open = "d" + (d.rows.length - 1);
      return true;
    }
    if (!card || !card.dataset.fold) return false;
    if (a === "fold") {
      open = open === card.dataset.fold ? null : card.dataset.fold;
      return true;
    }
    if (a === "delrow") {
      const i = +card.dataset.fold.slice(1);
      if (!confirm(`Delete the row ${JSON.stringify(d.rows[i].key || "")}?`)) return false;
      d.rows.splice(i, 1);
      open = null;
      return true;
    }
    if (!["rmt", "up", "down"].includes(a)) return false;
    const r = rowOf(card);
    const t = Array.isArray(r.targets) ? r.targets : (r.targets = []);
    const i = +btn.dataset.t;
    if (a === "rmt") t.splice(i, 1);
    else if (a === "up" && i > 0) [t[i - 1], t[i]] = [t[i], t[i - 1]];
    else if (a === "down" && i < t.length - 1) [t[i + 1], t[i]] = [t[i], t[i + 1]];
    return true;
  }

  const rows = { title: "Rows", file: "routing", html: form, edit, act };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("rows", rows);
  else (window.settingsSections = window.settingsSections || []).push(["rows", rows]);
})();
