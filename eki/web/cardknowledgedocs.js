// A knowledge card's documents, the editing half (cardknowledge.js draws the list and its file rows and
// asks this for each document's head and the "+ document" part). A document's head: its name, its read
// mode as a choicemenu.js dropdown (each choice explained from card.choice_help.read), its total size and
// newest change, its about line, a × to remove it (after a confirm) and what the server said about it.
// "+ document" opens a small form (name, files one per line, about, read). Every change saves the whole
// list with ctx.save({docs: [...], files: null, read: null}): an old one-document card (top-level files and
// read) becomes the docs shape on its first save. Read-only (no dropdown, ×, or + document) when the card
// can't be edited. The dropdowns are wired on the part's own box and their events stop there, so the card
// panel's own choiceMenu (for settings) never sees them.
(function () {
  const READ = ["always", "when-relevant"];
  const S = { add: null };                    // the + document form's values while it is open
  const choices = (h) => ((h.card.choices || {}).read || READ);
  const helps = (h) => ((h.card.choice_help || {}).read || {});

  // the list as the card stores it: each document's own patterns, in order, once each
  const patterns = (d) => (d.files || []).map((f) => f.pattern || f.path).filter((p, i, a) => p && a.indexOf(p) === i);
  const specs = (docs) => (docs || []).map((d) => ({ name: d.name, files: patterns(d), about: d.about || "", read: d.read }));

  // what the server said, by document ("document NAME: …" or "document N: …"); the rest under "" (the part's top)
  function problems(h) {
    const out = { "": [] }, pr = h.problems || {}, docs = h.docs || [];
    for (const k of ["", "docs", "files", "read"]) {
      for (const msg of String(pr[k] || "").split("; ").filter(Boolean)) {
        const m = /^document ([\w.-]+): /.exec(msg);
        let who = m ? m[1] : "";
        if (/^\d+$/.test(who)) who = (docs[Number(who) - 1] || {}).name || (S.add ? "+" : "");
        if (who && S.add && who === S.add.name && !docs.some((d) => d.name === who)) who = "+";
        (out[who] = out[who] || []).push(msg);
      }
    }
    return out;
  }
  const errs = (h, list) => (list || []).map((m) => `<div class="pj-err">${h.esc(m)}</div>`).join("");

  function head(d, i, h) {
    const cm = window.choiceMenu, ro = !h.editable;
    const read = cm ? cm.html("ckread:" + i, choices(h), d.read, helps(h), ro)
      : `<span class="dim">${h.esc(d.read || "")}</span>`;
    const meta = [h.size(d.size), h.ago(d.changed)].filter(Boolean).join(" · ");
    return `<div class="ck-doc-head"><strong class="ck-doc-name">${h.esc(d.name)}</strong>${read}
        <span class="dim ck-doc-meta">${h.esc(meta)}</span>
        ${ro ? "" : `<button type="button" class="pj-btn ck-x" data-ckd="remove" data-i="${i}" title="Remove the document ${h.esc(d.name)} (its files stay)">×</button>`}</div>
      ${d.about ? `<div class="ck-doc-about">${h.esc(d.about)}</div>` : ""}${errs(h, problems(h)[d.name])}`;
  }

  const top = (h) => errs(h, problems(h)[""]);

  function adder(h) {
    if (!h.editable) return "";
    if (!S.add) return '<button type="button" class="pj-btn" data-ckd="add">+ document</button>';
    const a = S.add, cm = window.choiceMenu;
    return `<div class="ck-addform">
      <input class="set-in ck-add-name" placeholder="name: letters, digits, _ . -" value="${h.esc(a.name)}" spellcheck="false">
      <textarea class="set-in ck-add-files" rows="2" placeholder="files, one per line: paths in the project, globs and folders allowed" spellcheck="false">${h.esc(a.files)}</textarea>
      <input class="set-in ck-add-about" placeholder="about: what it holds, when to read it" value="${h.esc(a.about)}">
      <div class="pj-row">${cm ? cm.html("ckread:new", choices(h), a.read, helps(h), false) : ""}
        <button type="button" class="pj-btn primary" data-ckd="addsave">Add</button>
        <button type="button" class="pj-btn" data-ckd="addcancel">Cancel</button></div>
      ${errs(h, problems(h)["+"])}</div>`;
  }

  // the form's typed values into S.add, so a redraw keeps them
  function grab(el) {
    if (!S.add || !el.querySelector) return;
    const v = (sel) => { const x = el.querySelector(sel); return x ? x.value : null; };
    for (const [k, sel] of [["name", ".ck-add-name"], ["files", ".ck-add-files"], ["about", ".ck-add-about"]]) {
      const got = v(sel);
      if (got != null) S.add[k] = got;
    }
  }

  async function save(h, list) {
    await h.save({ docs: list, files: null, read: null });
    const pr = h.problems || {};
    return !["", "docs", "files", "read"].some((k) => pr[k]);
  }

  // a click on a [data-ckd] button; redraw() draws the + document part again
  async function act(b, el, h, redraw) {
    if (!h.editable) return;
    const what = b.dataset.ckd;
    if (what === "add") { S.add = { name: "", files: "", about: "", read: "when-relevant" }; return redraw(); }
    if (what === "addcancel") { S.add = null; return redraw(); }
    if (what === "remove") {
      const d = (h.docs || [])[Number(b.dataset.i)];
      if (!d || !window.confirm(`Remove the document ${d.name} from this card? Its files stay in the project.`)) return;
      return save(h, specs(h.docs).filter((x) => x.name !== d.name));
    }
    if (what === "addsave" && S.add) {
      grab(el);
      const a = S.add, files = a.files.split("\n").map((s) => s.trim()).filter(Boolean);
      const ok = await save(h, specs(h.docs).concat({ name: a.name.trim(), files, about: a.about.trim(), read: a.read }));
      if (ok) S.add = null;
      redraw();
    }
  }

  // the read dropdowns of `el`: a pick saves that document's read (or sets the new one's)
  function wire(el, helper, redraw) {
    if (!window.choiceMenu || el.dataset.ckdWired) return;
    el.dataset.ckdWired = "1";
    choiceMenu.attach(el, (key, value) => {
      const h = helper();
      if (!h.editable) return;
      if (key === "ckread:new") { grab(el); if (S.add) S.add.read = value; return redraw(); }
      const list = specs(h.docs), i = Number(String(key).split(":")[1]);
      if (!list[i]) return;
      list[i].read = value;
      save(h, list);
    });
    const stop = (e) => { if (e.target.closest && e.target.closest(".cm")) e.stopPropagation(); };
    for (const ev of ["click", "keydown", "mouseover"]) el.addEventListener(ev, stop);
  }

  const reset = () => { S.add = null; };

  window.cardKnowledgeDocs = { head, top, adder, act, wire, reset, specs };
})();
