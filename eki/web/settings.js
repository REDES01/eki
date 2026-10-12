// Settings: routing.json and providers.json, as a form or as raw JSON, over one draft per file.
// It takes #main's place while the address is #settings or #settings/<section> (nav.js). This is
// the frame only: a list of sections down the left (#set-nav) next to one body, the bar, the
// drafts, the notes and the saves: every change is written as it is made, one POST per file at a time. Each section lives in its own file and registers itself with
// settings.section(name, {title, file, html(d), edit(el, d, card, ev), act(act, btn, card)}):
// file is the one it edits ("routing" or "providers"), html draws its form from that draft, and
// edit/act return true to draw it again (act may be async). The page decides nothing: the
// server checks the whole file before it writes (eki/api_settings.py), keeps the old one as
// <name>.json.bak, and refuses (409) a file that changed on disk since it was loaded.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const SECTIONS = ["jobs", "rows", "providers", "notifications", "machine", "self", "components", "sync", "pictures"];
  const FILES = { routing: "Routing", providers: "Providers" };
  const REG = {};
  const S = { on: false, sect: "jobs", mode: "form", got: {}, draft: {}, raw: {}, rawErr: {}, note: {} };
  const Q = {};   // per file: {timer, busy, again, stop (a 409: wait for a reload), bad (refused), ok}
  const q = (name) => Q[name] || (Q[name] = {});
  const clone = (x) => JSON.parse(JSON.stringify(x));

  function section(name, s) { REG[name] = s; if (S.on && name === S.sect) render(); }
  (window.settingsSections || []).forEach(([name, s]) => section(name, s));   // registered before us

  const sect = () => REG[S.sect];
  const file = () => (sect() ? sect().file : "routing");

  function box() {
    let el = $("setview");
    if (!el) {
      el = document.createElement("div");
      el.id = "setview";
      el.innerHTML = `<div class="set-bar">
          <div class="set-seg" id="set-modes"><button type="button" data-mode="form">Form</button><button type="button" data-mode="raw">Raw JSON</button></div>
          <span class="grow"></span>
          <button type="button" class="ghost" data-act="reset" title="Throw away the draft and read the file again">Reset to what's on disk</button>
          <span id="set-status"></span>
        </div>
        <div id="set-wrap"><nav id="set-nav"></nav><div id="set-main"><div id="set-note"></div><div id="set-body"></div></div></div>`;
      $("main").insertBefore(el, $("composer"));
      el.addEventListener("click", onClick);
      el.addEventListener("input", onEdit);
      el.addEventListener("change", onEdit);
      el.addEventListener("keydown", (e) => {   // a fold's summary line opens from the keyboard too
        if ((e.key === "Enter" || e.key === " ") && e.target.matches(".set-sum")) { e.preventDefault(); e.target.click(); }
      });
    }
    return el;
  }

  // the file as it is on disk; a draft with changes in it is kept unless `drop`
  async function load(name, drop) {
    const r = await fetch("/api/settings/" + name);
    const got = await r.json();
    if (!r.ok) throw new Error(got.error || r.statusText);
    if (!drop && dirty(name)) return;
    [S.got[name], S.draft[name], S.raw[name], S.rawErr[name]] = [got, clone(got.data), null, null];
  }

  const dirty = (name) => S.got[name] && JSON.stringify(S.draft[name]) !== JSON.stringify(S.got[name].data);

  const busy = (name) => !!(q(name).busy || q(name).timer);
  const unsaved = (name) => dirty(name) && !busy(name);

  function meta() {
    const g = S.got[file()], name = file();
    if (!S.on || !g) return;
    $("meta").textContent = g.path + (busy(name) && dirty(name) ? " · saving…" : unsaved(name) ? " · not saved" : "");
    const el = $("set-status"), w = q(name);
    if (!el) return;
    el.textContent = w.busy ? "Saving…" : w.bad ? "Not saved" : w.ok ? "Saved" : "";
    el.classList.toggle("bad", !w.busy && !!w.bad);
  }

  function note(name, kind, html) { S.note[name] = html ? { kind, html } : null; if (S.on && name === file()) showNote(); }

  function showNote() { const n = S.note[file()]; $("set-note").innerHTML = n ? `<div class="set-msg ${n.kind}">${n.html}</div>` : ""; }

  function drawNav() {
    $("set-nav").innerHTML = SECTIONS.filter((n) => REG[n]).map((n) =>
      `<button type="button" data-sect="${n}" class="${n === S.sect ? "on" : ""}">${esc(REG[n].title)}${unsaved(REG[n].file) ? ' <span class="set-dot" title="not saved">•</span>' : ""}</button>`).join("");
  }

  function render() {
    if (!S.on) return;
    box().querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
    drawNav();
    showNote();
    const s = sect(), name = file();
    if (!s) { $("set-body").innerHTML = '<div class="dim pad">Not available in this build.</div>'; return; }
    if (!S.got.routing || !S.got.providers) { $("set-body").innerHTML = '<div class="dim pad">Loading…</div>'; return; }
    if (S.mode === "raw") {
      const txt = S.raw[name] != null ? S.raw[name] : JSON.stringify(S.draft[name], null, 2);
      $("set-body").innerHTML = `<div id="set-rawerr" class="err"></div><textarea id="set-raw" spellcheck="false">${esc(txt)}</textarea>`;
      rawErr();
    } else {
      const top = $("set-body").scrollTop;
      $("set-body").innerHTML = `<div class="set-page" data-sect="${S.sect}">${s.html(S.draft[name])}</div>`;
      $("set-body").scrollTop = top;
    }
    meta();
  }

  function rawErr() {
    const e = $("set-rawerr"), m = S.rawErr[file()];
    if (e) e.textContent = m ? `Not valid JSON yet — ${m}. Nothing is sent until it parses.` : "";
  }

  function onEdit(ev) {
    const el = ev.target, name = file();
    if (el.id === "set-raw") {
      S.raw[name] = el.value;
      try { S.draft[name] = JSON.parse(el.value); S.rawErr[name] = null; } catch (e) { S.rawErr[name] = e.message; }
      rawErr();
      if (!S.rawErr[name]) queue(name, ev.type === "input" ? 600 : 0);
      drawNav();
      return meta();
    }
    if (!el.matches("input, select, textarea") || !sect() || !sect().edit) return;
    if (ev.type === "input" && (el.type === "checkbox" || el.tagName === "SELECT")) return;   // their change event does it
    const card = el.closest(".set-card, .set-fold");
    const redraw = sect().edit(el, S.draft[name], card, ev);
    queue(name, ev.type === "input" ? 600 : 0);
    if (redraw) render(); else { meta(); drawNav(); }
  }

  async function onClick(ev) {
    let b = ev.target.closest("button");
    const sum = !b && !ev.target.closest("input, select, textarea, label, a") && ev.target.closest(".set-sum");
    if (sum) b = sum.closest(".set-fold");   // a fold's summary line: the section's act("fold")
    if (!b) return;
    if (b.dataset.sect) { location.hash = "#settings/" + b.dataset.sect; return; }
    if (b.dataset.mode) {
      const bad = Object.keys(FILES).find((n) => S.rawErr[n]);
      if (b.dataset.mode === "form" && bad) return note(file(), "err", `Fix the ${FILES[bad]} JSON first: the form shows the draft, and it doesn't parse.`);
      S.mode = b.dataset.mode;
      S.raw = {};
      return render();
    }
    const act = sum ? "fold" : b.dataset.act;
    if (act === "reset" || act === "reload") {
      if (act === "reset" && dirty(file()) && !confirm("Throw away the changes in this draft?")) return;
      return reload(file(), "Back to what's on disk.");
    }
    const card = b.closest(".set-card, .set-fold"), name = file(), was = JSON.stringify(S.draft[name]);
    const changed = () => { if (JSON.stringify(S.draft[name]) !== was) queue(name, 0); };
    if (window.settingsModels && await settingsModels.act(act, b, (h) => note(name, "err", h))) { changed(); return render(); }
    const redraw = sect() && sect().act && await sect().act(act, b, card);
    changed();
    if (redraw) render(); else { meta(); drawNav(); }
  }

  async function reload(name, said) {
    clearTimeout(q(name).timer);
    Object.assign(q(name), { timer: null, again: false, stop: false, bad: false, ok: false });
    try {
      await Promise.all([load(name, true), window.settingsModels && settingsModels.load()]);
      note(name, "info", esc(said || ""));
    } catch (e) {
      note(name, "err", "Can't read the settings: " + esc(e.message));
    }
    render();
  }

  // a change saves itself: at once (delayMs 0) or after a pause in typing; a newer change restarts the wait
  function queue(name, delayMs) {
    if (!S.got[name] || S.rawErr[name] || q(name).stop) return;
    clearTimeout(q(name).timer);
    q(name).timer = setTimeout(() => save(name), delayMs);
    meta(); drawNav();
  }

  // one POST in flight per file; a change made meanwhile goes once it returns, with the mtime it gave
  async function save(name) {
    const w = q(name);
    w.timer = null;
    if (w.busy) { w.again = true; return; }
    if (w.stop || S.rawErr[name]) return;
    if (!dirty(name)) {
      if (w.bad) { w.bad = false; clearNote(name); }
      return meta(), drawNav();
    }
    const sent = clone(S.draft[name]);
    [w.busy, w.ok] = [true, false];
    meta(); drawNav();
    try {
      const r = await fetch("/api/settings/" + name, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
        body: JSON.stringify({ data: sent, mtime: S.got[name].mtime }),
      });
      const got = await r.json().catch(() => ({}));
      w.bad = !r.ok;
      if (r.status === 409) {
        w.stop = true;
        note(name, "warn", `${esc(got.error || "The file changed on disk since it was loaded.")} Your draft is kept here.
          <button type="button" class="ghost small" data-act="reload">Reload from disk</button>`);
      } else if (r.status === 400 && Array.isArray(got.problems)) {
        note(name, "err", `Not saved yet — ${got.problems.length} problem${got.problems.length === 1 ? "" : "s"}:<ul>${got.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`);
      } else if (!r.ok) {
        note(name, "err", "Not saved: " + esc(got.error || r.statusText));
      } else {
        S.got[name] = { ...S.got[name], data: sent, mtime: got.mtime };   // no redraw: focus and folds stay
        w.ok = true;
        clearNote(name);
      }
    } catch (e) {
      w.bad = true;
      note(name, "err", "Not saved: " + esc(e.message));
    } finally {
      w.busy = false;
    }
    if (w.again && !w.stop) { w.again = false; return save(name); }
    w.again = false;
    meta(); drawNav();
  }

  function clearNote(name) { if (S.note[name] && S.note[name].kind === "err") note(name, null); }

  // arg: the section from #settings/<section>; none (or one we don't know) is Jobs. A draft with
  // changes survives a change of section and leaving Settings; a file without changes is read again.
  async function show(arg) {
    S.sect = SECTIONS.includes(arg) ? arg : "jobs";
    [S.on, box().hidden] = [true, false];
    [$("title").textContent, $("meta").textContent] = ["Settings", ""];
    render();
    try {
      await Promise.all(Object.keys(FILES).map((n) => load(n)).concat(window.settingsModels ? [settingsModels.load()] : []));
    } catch (e) {
      note(file(), "err", "Can't read the settings: " + esc(e.message));
    }
    render();
  }

  function hide() { if (S.on) { S.on = false; box().hidden = true; } }

  window.settings = {
    show, hide, on: () => S.on, section, render, clone, note,
    draft: (name) => S.draft[name], got: (name) => S.got[name],
    sections: () => SECTIONS.filter((n) => REG[n]),
  };
  nav.register("settings", { show, hide });
})();
