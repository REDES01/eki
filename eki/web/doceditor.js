// The doc editor in the side panel: a plain markdown text box over a file from GET /api/doc, saved with
// POST /api/doc {path, text, base_hash} on ⌘S/Ctrl-S, when you leave the box, and on Done. Never loses
// text: a save that finds the file changed on disk (an agent wrote it) keeps what you typed and shows
// "changed on disk" with Reload (take the disk text), Keep mine (write yours over it) and Show both (mine
// left, disk right); a failed save shows its error and keeps the text too. Tab / Shift-Tab indent and
// outdent by two spaces, on one line or a selection of lines.
//   doceditor.open(body, doc, done)  draws the editor in `body`; done(latestDoc) runs after Done saves
//   doceditor.flush()                 a Promise, settled once unsaved text is saved (or a conflict shows)
//   doceditor.dirty()                 whether the box holds text not yet saved
(function () {
  const CSS = `
.de { position: relative; display: flex; flex-direction: column; height: 100%; }
.de-bar, .de-note { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-bottom: 1px solid var(--line);
  font-size: 13px; }
.de-bar .de-hint { color: var(--dim); flex: 1; }
.de-note { background: var(--code); }
.de-note .de-msg { flex: 1; color: var(--red); }
.de button { border: 1px solid var(--line); background: var(--card); border-radius: 6px; padding: 2px 10px; cursor: pointer;
  font-size: 13px; }
.de button:hover { border-color: var(--teal); }
.de-text { flex: 1; width: 100%; resize: none; border: 0; outline: 0; padding: 12px 16px; background: var(--bg);
  font: 13px/1.6 ui-monospace, Menlo, monospace; tab-size: 2; }
.de-both { flex: 1; display: flex; min-height: 0; }
.de-both > div { flex: 1; overflow: auto; min-width: 0; }
.de-both > div + div { border-left: 1px solid var(--line); }
.de-both h4 { margin: 0; padding: 6px 12px; font-size: 12px; color: var(--dim); border-bottom: 1px solid var(--line); }
.de-both pre { padding: 10px 12px; }
.de-toast { position: absolute; right: 14px; bottom: 14px; background: var(--text); color: var(--bg); border-radius: 6px;
  padding: 4px 12px; font-size: 13px; pointer-events: none; }
`;
  let E = null;                                      // the open editor, one at a time

  function css() {
    if (document.getElementById("doceditor-css")) return;
    const s = document.createElement("style");
    s.id = "doceditor-css"; s.textContent = CSS;
    document.head.appendChild(s);
  }
  function mk(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function button(text, fn) {
    const b = mk("button", "", text);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }
  const ratioOf = (el) => {
    const room = (el.scrollHeight || 0) - (el.clientHeight || 0);
    return room > 0 ? (el.scrollTop || 0) / room : 0;
  };

  function open(body, doc, done) {
    css();
    const ratio = ratioOf(body);
    const S = { body, doc, done, path: doc.path, saved: doc.text || "", hash: doc.hash, mtime: doc.mtime,
                conflict: null, saving: null, timer: 0, both: false };
    const ro = doc.editable === false;
    S.wrap = mk("div", "de");
    const bar = mk("div", "de-bar");
    bar.appendChild(mk("span", "de-hint", ro ? (doc.reason || "read-only") : "saved on ⌘S or when you leave the box"));
    bar.appendChild(button("Done", () => finish(S)));
    S.note = mk("div", "de-note"); S.note.hidden = true;
    S.area = mk("textarea", "de-text");
    S.area.value = S.saved; S.area.spellcheck = false; S.area.readOnly = ro;
    S.area.addEventListener("keydown", (e) => key(S, e));
    S.area.addEventListener("blur", () => { save(S); });
    S.split = mk("div", "de-both"); S.split.hidden = true;
    S.toast = mk("div", "de-toast"); S.toast.hidden = true;
    for (const el of [bar, S.note, S.area, S.split, S.toast]) S.wrap.appendChild(el);
    body.innerHTML = "";
    body.appendChild(S.wrap);
    S.area.scrollTop = ratio * Math.max(0, (S.area.scrollHeight || 0) - (S.area.clientHeight || 0));
    E = S;
    return S.area;
  }

  function key(S, e) {
    if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === "s") { e.preventDefault(); save(S); return; }
    if (e.key === "Tab" && !e.metaKey && !e.ctrlKey && !e.altKey && !S.area.readOnly) {
      e.preventDefault();
      indent(S.area, e.shiftKey);
    }
  }

  // Tab with no selection puts two spaces at the cursor; with a selection (or Shift) it works on whole lines.
  function indent(a, out) {
    const v = a.value, s = a.selectionStart, e = a.selectionEnd;
    if (!out && s === e) {
      a.value = v.slice(0, s) + "  " + v.slice(e);
      a.selectionStart = a.selectionEnd = s + 2;
      return;
    }
    const from = v.lastIndexOf("\n", s - 1) + 1;
    const to = e > s && v[e - 1] === "\n" ? e - 1 : e;           // a selection ending at a line start leaves that line
    const end = v.indexOf("\n", to) < 0 ? v.length : v.indexOf("\n", to);
    let first = 0, total = 0;
    const lines = v.slice(from, end).split("\n").map((l, i) => {
      const n = out ? (l.startsWith("  ") ? -2 : l.startsWith(" ") ? -1 : 0) : 2;
      if (i === 0) first = n;
      total += n;
      return n < 0 ? l.slice(-n) : " ".repeat(n) + l;
    });
    a.value = v.slice(0, from) + lines.join("\n") + v.slice(end);
    const ns = Math.max(from, s + first);
    a.selectionStart = ns;
    a.selectionEnd = Math.max(ns, e + total);
  }

  async function post(S, text, base) {
    let got;
    try {
      const r = await fetch("/api/doc", { method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
        body: JSON.stringify({ path: S.path, text, base_hash: base }) });
      got = await r.json().catch(() => ({}));
      if (!got.saved && !got.conflict && !got.error) got.error = "save failed" + (r.status ? ` (${r.status})` : "");
    } catch (err) {
      got = { error: "save failed: " + ((err && err.message) || "no connection") };
    }
    if (got.saved) {
      S.saved = text; S.hash = got.hash; S.mtime = got.mtime; S.conflict = null;
      S.note.hidden = true; showBoth(S, false);
      toast(S, "Saved");
      return true;
    }
    if (got.conflict) { S.conflict = got; conflict(S); return false; }
    S.note.innerHTML = "";
    S.note.appendChild(mk("span", "de-msg", got.error));
    S.note.hidden = false;
    return false;
  }

  // One save at a time; a save asked for while one runs waits for it, then posts whatever is still unsaved.
  async function save(S) {
    while (S.saving) await S.saving;
    if (S.conflict || S.area.readOnly || S.area.value === S.saved) return !S.conflict;
    const p = post(S, S.area.value, S.hash);
    S.saving = p;
    try { return await p; } finally { if (S.saving === p) S.saving = null; }
  }

  async function keepMine(S) {
    while (S.saving) await S.saving;
    if (!S.conflict) return;
    const p = post(S, S.area.value, S.conflict.hash);
    S.saving = p;
    try { await p; } finally { if (S.saving === p) S.saving = null; }
  }

  function reload(S) {
    const c = S.conflict;
    if (!c) return;
    S.area.value = c.text || ""; S.saved = S.area.value; S.hash = c.hash; S.mtime = c.mtime;
    S.conflict = null; S.note.hidden = true; showBoth(S, false);
  }

  function conflict(S) {
    S.note.innerHTML = "";
    S.note.appendChild(mk("span", "de-msg", "changed on disk"));
    S.note.appendChild(button("Reload", () => reload(S)));
    S.note.appendChild(button("Keep mine", () => keepMine(S)));
    S.note.appendChild(S.both ? button("Back", () => showBoth(S, false)) : button("Show both", () => showBoth(S, true)));
    S.note.hidden = false;
    if (S.both) showBoth(S, true);
  }

  function showBoth(S, on) {
    const was = S.both;
    S.both = on && !!S.conflict;
    S.split.innerHTML = "";
    if (S.both) {
      for (const [title, text] of [["Mine", S.area.value], ["On disk", S.conflict.text || ""]]) {
        const col = mk("div");
        col.appendChild(mk("h4", "", title));
        col.appendChild(mk("pre", "", text));
        S.split.appendChild(col);
      }
    }
    S.split.hidden = !S.both;
    S.area.hidden = S.both;
    if (S.conflict && was !== S.both) conflict(S);       // swap Show both ↔ Back
  }

  function toast(S, text) {
    S.toast.textContent = text;
    S.toast.hidden = false;
    clearTimeout(S.timer);
    S.timer = setTimeout(() => { S.toast.hidden = true; }, 1500);
  }

  async function finish(S) {
    await save(S);
    if (S.conflict || S.area.value !== S.saved) return;   // stay until the text is safe
    if (E === S) E = null;
    if (S.done) S.done(Object.assign({}, S.doc, { text: S.saved, mtime: S.mtime, hash: S.hash }));
  }

  window.doceditor = {
    open,
    flush: () => (E ? save(E).then(() => undefined) : Promise.resolve()),
    dirty: () => !!E && E.area.value !== E.saved,
  };
})();
