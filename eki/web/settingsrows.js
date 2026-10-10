// Settings → Rows: routing.json's rows, one card each, every target one picker
// (settingstarget.js). Rows eki adds only in memory (table.rows()) are shown as such and join
// the draft when first edited. Registers the "rows" section with settings.js.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const ABILITIES = ["text", "tools", "web", "vision", "image", "image-edit"];   // providers.ABILITIES

  function addedRows() {
    const have = new Set((settings.draft("routing").rows || []).map((r) => r && r.key));
    return (settings.got("routing").effective.rows || []).filter((r) => r.added && !have.has(r.key));
  }

  function rowCard(r, id, added) {
    const { check, text, field, memo } = settingsForm;
    const [targets, needs] = [r.targets, r.needs].map((x) => (Array.isArray(x) ? x : []));
    const names = settingsProviders.names(), models = window.settingsModels ? settingsModels.models() : null;
    const last = targets.length - 1;
    return `<div class="set-card${added ? " added" : ""}" data-row="${esc(id)}">
      <div class="set-top">${text("key", r.key, "key")}${added ? memo : ""}<span class="grow"></span>
        ${added ? "" : '<button type="button" class="ghost small" data-act="delrow" title="Delete this row">Delete</button>'}</div>
      ${field("Title", text("title", r.title, "what this row is for"))}
      ${field("Needs", `<div class="set-checks">${ABILITIES.map((a) => check(needs.includes(a), `data-need="${a}"`, a)).join("")}</div>`)}
      ${field("Examples <i class=\"dim\">one per line</i>", `<textarea class="set-in" data-f="examples" rows="${Math.max(2, (r.examples || []).length)}">${esc((r.examples || []).join("\n"))}</textarea>`)}
      ${field("Targets <i class=\"dim\">first that can answer wins</i>", `<ol class="set-targets">${targets.map((t, i) => `<li>${settingsTarget.html(t, { names, models, attrs: `data-t="${i}"` })}<span class="grow"></span>
        <button type="button" class="ghost small" data-act="up" data-t="${i}" ${i ? "" : "disabled"} title="Earlier">↑</button>
        <button type="button" class="ghost small" data-act="down" data-t="${i}" ${i < last ? "" : "disabled"} title="Later">↓</button>
        <button type="button" class="ghost small" data-act="rmt" data-t="${i}" title="Remove">✕</button></li>`).join("")}</ol>
        <select class="set-in" data-f="addt"><option value="">＋ add a target…</option>${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select>`)}
    </div>`;
  }

  function form(d) {
    return `<section class="set-sect"><h3>Rows</h3>
        ${(d.rows || []).map((r, i) => rowCard(r || {}, "d" + i, false)).join("")}
        ${addedRows().map((r) => rowCard(r, "a:" + r.key, true)).join("")}
        <button type="button" class="ghost" data-act="addrow">＋ Add a row</button></section>`;
  }

  // the draft row behind a card; an in-memory row joins the draft when it is first edited
  function rowOf(card) {
    const d = settings.draft("routing"), id = card.dataset.row;
    if (!Array.isArray(d.rows)) d.rows = [];
    if (id[0] === "d") return d.rows[+id.slice(1)];
    const r = settings.clone(addedRows().find((x) => x.key === id.slice(2)));
    delete r.added;
    d.rows.push(r);
    card.dataset.row = "d" + (d.rows.length - 1);
    card.classList.remove("added");
    card.querySelector(".set-mem")?.remove();
    return r;
  }

  function edit(el, d, card) {
    const f = el.dataset.f;
    if (!card || !card.dataset.row) return false;
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
    } else if (f === "key" || f === "title") r[f] = el.value;
    return false;
  }

  function act(a, btn, card) {
    const d = settings.draft("routing");
    if (a === "addrow") {
      (Array.isArray(d.rows) ? d.rows : (d.rows = [])).push({ key: "", title: "", examples: [], needs: ["text"], targets: [] });
      return true;
    }
    if (!card || !card.dataset.row) return false;
    if (a === "delrow") {
      const i = +card.dataset.row.slice(1);
      if (!confirm(`Delete the row ${JSON.stringify(d.rows[i].key || "")}? (Only in the draft until you save.)`)) return false;
      d.rows.splice(i, 1);
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
  if (window.settings) settings.section("rows", rows);
  else (window.settingsSections = window.settingsSections || []).push(["rows", rows]);
})();
