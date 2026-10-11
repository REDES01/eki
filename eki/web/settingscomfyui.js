// Settings → Providers → a ComfyUI entry: its workflows, listed the way a model provider lists its
// models, over GET /api/comfyui/workflows (each workflow's file under ~/.eki/comfyui/workflows/,
// read for its inputs, models, sampler, steps and size). settingsproviders.js puts line() on the
// comfyui entry's summary line and body() inside its open fold, and hands it every act and edit
// first (act/edit say whether they took it). Each workflow folds to one line (one open at a time);
// open, it shows what the file declares and its buttons: Open in ComfyUI, Reload from file, Try
// (one field per input, prompt first), Remove. Label and default edits POST .../edit at once; none
// of this is in providers.json's draft. Errors come back as {"error"} and show in amber.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const API = "/api/comfyui/workflows";
  const W = { got: null, err: "", loading: false, open: null, tries: {}, said: {}, add: { path: "", out: null } };
  const amber = (s) => (s ? `<div class="set-amber">${esc(s)}</div>` : "");
  const redraw = () => window.settings && settings.render();

  async function call(path, body) {
    const r = await fetch(API + path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  async function load() {
    W.loading = true;
    try { [W.got, W.err] = [await call(""), ""]; } catch (e) { W.err = "Can't read the workflows: " + e.message; }
    W.loading = false;
    redraw();
  }

  // read once, the first time a ComfyUI entry is drawn; the buttons read again after a change
  const need = () => { if (!W.got && !W.loading && !W.err) load(); };
  const list = () => (W.got && W.got.workflows) || [];
  const byId = (id) => list().find((w) => w.id === id);

  // the summary line's count, like settingsModels.line
  function line() {
    need();
    if (!W.got) return W.err ? '<span class="dim set-amber">workflows not read</span>' : "";
    const n = list().length;
    return `<span class="dim">${n} workflow${n === 1 ? "" : "s"}</span>`;
  }

  // ---- one workflow ------------------------------------------------------------------------

  const where = (i) => (i.source ? `<code>${esc(i.source)}</code>` : `node ${esc(i.node)} · ${esc(i.field)}`);
  const size = (w) => (w.width && w.height ? `${w.width}×${w.height}` : "—");
  const btn = (act, id, label, cls) => `<button type="button" class="ghost small${cls ? " " + cls : ""}" data-act="${act}" data-wf="${esc(id)}">${label}</button>`;

  function inputsHtml(w) {
    const { line: ln, lines } = settingsForm;
    if (!(w.inputs || []).length) return lines("Inputs", ln('<span class="dim">none found — no {{…}} placeholders</span>', ""));
    return lines("Inputs", w.inputs.map((i) => ln(`<b>${esc(i.name)}</b>`,
      `<input class="set-in" data-wfdef="${esc(i.name)}" value="${esc(i.default == null ? "" : i.default)}" placeholder="no default" spellcheck="false">`,
      where(i))).join(""));
  }

  function modelsHtml(w) {
    const { line: ln, lines } = settingsForm;
    const ms = w.models || [];
    const rows = ms.length ? ms.map((m) => ln(`${esc(m.name)}${m.found === false ? ' <span class="set-pill set-amber" title="not in ComfyUI\'s model folders">not found</span>' : ""}`,
      "", `node ${esc(m.node)} · ${esc(m.field)}`)).join("") : ln('<span class="dim">none named in the file</span>', "");
    const sampler = [w.sampler, w.steps ? `${w.steps} steps` : ""].filter(Boolean).join(" · ") || "—";
    return lines("Models", rows) + lines("Reads", ln("Sampler", `<span class="dim">${esc(sampler)}</span>`)
      + ln("Size", `<span class="dim">${esc(size(w))}</span>`)
      + (w.needs_source ? ln("Source picture", '<span class="dim">needed</span>', "it edits or upscales a picture you give it") : ""));
  }

  // Try: one field per input, prompt first; the run it started links to its thread
  function tryHtml(w) {
    const { block, lines } = settingsForm;
    const t = W.tries[w.id] || (W.tries[w.id] = { inputs: {}, out: null, busy: false });
    const names = (w.inputs || []).map((i) => i.name).sort((a, b) => (b === "prompt") - (a === "prompt"));
    const fields = names.map((n) => {
      const i = w.inputs.find((x) => x.name === n), v = t.inputs[n] != null ? t.inputs[n] : "";
      const ph = i.default == null || i.default === "" ? n : String(i.default);
      return n === "prompt"
        ? `<textarea class="set-in" rows="2" data-wftry="${esc(n)}" placeholder="prompt" spellcheck="false">${esc(v)}</textarea>`
        : `<label class="set-field"><span>${esc(n)}</span><input class="set-in" data-wftry="${esc(n)}" value="${esc(v)}" placeholder="${esc(ph)}" spellcheck="false"></label>`;
    }).join("");
    const out = t.out && (t.out.error ? amber(t.out.error)
      : `<div class="dim">Started run <code>${esc(t.out.run)}</code>${t.out.thread ? ` — <a href="#${esc(encodeURIComponent(t.out.thread))}">open its thread</a>` : ""}</div>`);
    return lines("Try", block("Inputs → pictures", `<div class="set-wftry">${fields}</div>
      ${btn("wftry", w.id, t.busy ? "Starting…" : "Try", "")}${out || ""}`, "empty fields take the defaults"));
  }

  function body1(w) {
    const { line: ln, lines, text, danger } = settingsForm;
    const said = W.said[w.id];
    return lines("", ln("Label", text("wflabel", w.label || "", w.id))
        + ln("File", `<span class="dim">${esc(w.file || "")}</span>`, w.path ? `<code>${esc(w.path)}</code>` : "")
        + (w.error ? ln("", amber(w.error)) : "")
        + ln("", `${btn("wfopen", w.id, "Open in ComfyUI")} ${btn("wfreload", w.id, "Reload from file")}`,
          said ? (said.error ? amber(said.error) : esc(said.info)) : "opens it at 127.0.0.1:8188, starting ComfyUI if it is off"))
      + inputsHtml(w) + modelsHtml(w) + tryHtml(w)
      + lines("", danger("Remove this workflow", "wfremove", "the file stays where it is; eki stops offering it")
        .replace('data-act="wfremove"', `data-act="wfremove" data-wf="${esc(w.id)}"`));
  }

  function fold(w) {
    const sum = `<b>${esc(w.label || w.id)}</b><span class="dim">${esc(w.id)}</span><span class="grow"></span>
      ${w.error ? '<span class="set-pill set-amber">error</span>' : ""}<span class="dim">${esc(w.file || "")}</span>`;
    return settingsForm.fold("wf:" + w.id, sum, W.open === w.id ? body1(w) : "", W.open === w.id)
      .replace('class="set-fold', 'class="set-fold set-wf');
  }

  // ---- the list, inside the comfyui entry's open fold --------------------------------------

  function addHtml() {
    const a = W.add, o = a.out;
    const res = !o ? "" : o.error ? amber(o.error)
      : `<div class="dim">${o.added.length ? `Added ${o.added.map((x) => `<code>${esc(x)}</code>`).join(", ")}.` : "Nothing added."}</div>
        ${(o.skipped || []).length ? `<ul class="set-wfskip">${o.skipped.map((s) => `<li><code>${esc(s.path)}</code> — ${esc(s.why)}</li>`).join("")}</ul>` : ""}`;
    return settingsForm.block("Add a workflow",
      `<div class="set-wfadd"><input class="set-in" data-wfaddpath value="${esc(a.path)}" placeholder="~/ai/game/tools/art/portrait.json or a folder" spellcheck="false">
       <button type="button" class="ghost small" data-act="wfadd">Add</button></div>${res}`,
      "a file, or a folder such as the game's tools to look through; a file must be exported from ComfyUI with Save (API)");
  }

  function body() {
    need();
    const { lines } = settingsForm;
    const head = `<div class="set-ftitle">Workflows${W.got && W.got.folder ? ` <span class="set-wfdir">· ${esc(W.got.folder)}</span>` : ""}</div>`;
    if (!W.got) return `<div class="set-flines">${head}<div class="set-line dim">${W.err ? amber(W.err) : "Reading the workflows…"}</div></div>`;
    const folds = list().map(fold).join("") || '<div class="set-line dim">No workflows yet.</div>';
    return `<div class="set-flines set-wfs">${head}<div class="set-wflist">${folds}</div></div>` + lines("", addHtml());
  }

  // ---- buttons and fields ------------------------------------------------------------------

  const wfOf = (el) => { const c = el.closest(".set-wf"); return c ? c.dataset.fold.slice(3) : el.dataset.wf; };

  // a click: false when it isn't ours, else true or a promise of whether to redraw
  function act(a, b, card) {
    if (a === "fold" && card && card.classList.contains("set-wf")) {
      const id = card.dataset.fold.slice(3);
      W.open = W.open === id ? null : id;
      return true;
    }
    return /^wf/.test(a || "") ? run(a, b) : false;
  }

  async function run(a, b) {
    const id = b.dataset.wf;
    b.disabled = true;
    try {
      if (a === "wfadd") {
        if (!W.add.path.trim()) throw new Error("Give a file or a folder.");
        const got = await call("/add", { path: W.add.path.trim() });
        W.add.out = { added: got.added || [], skipped: got.skipped || [] };
        if (got.workflows) W.got = { ...W.got, workflows: got.workflows };
        if ((got.added || []).length) W.add.path = "";
      } else if (a === "wfopen") {
        const got = await call("/open", { id });
        if (got.url) window.open(got.url, "_blank");
        W.said[id] = { info: `Opened ${got.url || ""}${got.copied ? " — the workflow is on the clipboard, paste it there" : ""}` };
      } else if (a === "wfreload") {
        await call("/reload", { id });
        W.said[id] = { info: "Read again from its file." };
        await load();
      } else if (a === "wfremove") {
        if (!confirm(`Remove the workflow ${(byId(id) || {}).label || id}?`)) { b.disabled = false; return false; }
        await call("/remove", { id });
        if (W.open === id) W.open = null;
        await load();
      } else if (a === "wftry") {
        const t = W.tries[id];
        const inputs = Object.fromEntries(Object.entries(t.inputs).filter(([, v]) => String(v).trim() !== ""));
        t.busy = true; redraw();
        try { t.out = await call("/try", { id, inputs }); } catch (e) { t.out = { error: "Not started: " + e.message }; }
        t.busy = false;
      }
    } catch (e) {
      if (a === "wfadd") W.add.out = { error: "Not added: " + e.message };
      else W.said[id] = { error: e.message };
    }
    return true;
  }

  // a field of ours: true when we took it (nothing here touches providers.json's draft)
  function edit(el, ev) {
    if (el.matches("[data-wfaddpath]")) { W.add.path = el.value; return true; }
    const id = wfOf(el);
    if (!id || !el.closest(".set-wf")) return false;
    if (el.dataset.wftry) { (W.tries[id] || (W.tries[id] = { inputs: {} })).inputs[el.dataset.wftry] = el.value; return true; }
    if (ev.type !== "change") return true;   // label and defaults save when the field is left
    const body = el.dataset.f === "wflabel" ? { id, label: el.value.trim() }
      : el.dataset.wfdef ? { id, defaults: { [el.dataset.wfdef]: el.value } } : null;
    if (body) call("/edit", body).then((got) => {
      W.said[id] = { info: "Saved." };
      if (got.workflows) W.got = { ...W.got, workflows: got.workflows }; else return load();
    }).catch((e) => { W.said[id] = { error: "Not saved: " + e.message }; }).then(redraw);
    return true;
  }

  window.settingsComfy = { load, line, body, act, edit };
})();
