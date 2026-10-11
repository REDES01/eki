// A picture card's own part in an open card (projectcardpanel.js draws it under "Pictures"): a service card
// {"target": "comfyui:<workflow>", "out": "…"} (or the old {"provider": "comfyui"}, read as <provider>:draft).
// From card.picture (eki/api_cardpicture.py): the target picker (settingstarget.js over GET /api/models,
// offering only ComfyUI providers, so it reads ComfyUI → <workflow>), saved as "target" (the old "provider"
// dropped); the workflow's inputs (name, default, flag) and models (a missing one marked); the out folder,
// saved as "out"; a Try box (prompt plus one field per other input) that runs the card through …/run with
// `--prompt … --<input> …` and polls …/uses/<run>; and the last pictures, each with its prompt and task.
// Any other service draws nothing here (its "Pictures" title hidden).
(function () {
  window.projectCardKinds = window.projectCardKinds || {};
  if (document.querySelector && !document.querySelector('link[href="/ui/cardpicture.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardpicture.css";
    document.head.appendChild(l);
  }
  if (document.querySelector && !window.settingsTarget && !document.querySelector('script[src="/ui/settingstarget.js"]')) {
    const t = document.createElement("script");
    t.src = "/ui/settingstarget.js"; t.async = false;
    t.onload = () => window.projectCardPanel && projectCardPanel.redraw();
    document.head.appendChild(t);
  }
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { el: null, card: null, pid: null, ctx: null, ro: false, models: null, loading: false, last: {}, run: null };

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }
  const url = (tail) => "/api/projects/" + encodeURIComponent(S.pid) + "/cards/" + encodeURIComponent(S.card.name) + (tail || "");
  const img = (p) => "/api/file?path=" + encodeURIComponent(p);
  const quote = (s) => "'" + String(s).replace(/'/g, `'"'"'`) + "'";        // for the server's shlex.split
  const ended = (d) => !!(d && (d.ended || ["done", "failed", "error", "cancelled", "canceled", "killed"].includes(d.state)));
  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(0, Date.now() / 1000 - t);
    return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  };
  const show = (v) => v == null || v === "" ? '<span class="dim">—</span>' : esc(typeof v === "object" ? JSON.stringify(v) : v);

  function put(sel, html, keepFocus) {
    const box = S.el && S.el.querySelector(sel), a = document.activeElement;
    if (!box || S.last[sel] === html) return;
    if (keepFocus && a && box.contains(a)) return;              // what you are typing stays
    box.innerHTML = html; S.last[sel] = html;
  }

  async function loadModels() {
    if (S.models || S.loading) return;
    S.loading = true;
    try { S.models = (await api("/api/models")).providers || {}; } catch (x) { S.models = {}; }
    S.loading = false;
    if (S.card) draw(S.el, S.card, S.pid, S.ro, S.ctx);
  }

  function picker(pic) {
    if (!window.settingsTarget || !S.models) return '<span class="dim">Loading the workflows…</span>';
    const names = Object.keys(S.models).filter((n) => S.models[n].kind === "comfyui");
    const html = settingsTarget.html(pic.target, { names, models: S.models });
    return S.editable ? html : html.replace(/<select /g, "<select disabled ");
  }

  function workflow(w) {
    if (!w) return "";
    if (w.error && !w.inputs) return `<div class="pj-err">${esc(w.error)}</div>`;
    const ins = (w.inputs || []).map((i) => `<tr><td><code>${esc(i.name)}</code></td><td>${show(i.default)}</td>
        <td><code>--${esc(i.name)}</code></td></tr>`).join("");
    const mods = (w.models || []).map((m) => `<li class="${m.found === false ? "cp-missing" : ""}"><code>${esc(m.name)}</code>
        ${m.found === false ? '<span class="pj-err">not found in ComfyUI\'s model folders</span>' : ""}</li>`).join("");
    const facts = [w.sampler, w.steps ? `${w.steps} steps` : "", w.width && w.height ? `${w.width}×${w.height}` : ""].filter(Boolean);
    return `${w.error ? `<div class="pj-err">${esc(w.error)}</div>` : ""}
      <div class="dim cp-file" title="${esc(w.path || "")}">${esc(w.label || w.id)} · <code>${esc(w.file || "")}</code>${facts.length ? " · " + esc(facts.join(" · ")) : ""}</div>
      <div class="pp-label">Inputs</div>
      ${ins ? `<table class="cp-inputs"><tr><th>name</th><th>default</th><th>flag</th></tr>${ins}</table>` : '<div class="dim">None: it takes no inputs.</div>'}
      <div class="pp-label">Models</div>
      ${mods ? `<ul class="cp-models">${mods}</ul>` : '<div class="dim">None read from its graph.</div>'}`;
  }

  function outPart(pic) {
    const spec = S.card.spec || {}, off = S.editable ? "" : " disabled", bad = (S.ctx && S.ctx.problems) || {};
    return `<div class="pp-label">Saves into</div>
      <input class="set-in cp-out" value="${esc(spec.out || "")}" placeholder="assets/generated/${esc(S.card.name)}/" spellcheck="false"${off}>
      <div class="dim cp-abs"><code>${esc(pic.out)}</code></div>
      ${bad.out ? `<div class="pj-err">${esc(bad.out)}</div>` : ""}${bad.target ? `<div class="pj-err">${esc(bad.target)}</div>` : ""}`;
  }

  function tryForm(w) {
    const others = ((w && w.inputs) || []).filter((i) => i.name !== "prompt");
    const off = S.ro ? " disabled" : "";
    return `<form class="cp-try" autocomplete="off">
      <textarea class="set-in cp-prompt" rows="3" placeholder="what to draw" spellcheck="false"${off}></textarea>
      ${others.map((i) => `<label class="cp-field"><span>${esc(i.name)}</span>
        <input class="set-in" data-input="${esc(i.name)}" placeholder="${esc(i.default == null ? "" : i.default)}" spellcheck="false"${off}></label>`).join("")}
      <button type="submit" class="pj-btn primary"${off}>Try</button></form>`;
  }

  function tryOut() {
    const r = S.run;
    if (!r) return "";
    if (r.err) return `<div class="pj-err">${esc(r.err)}</div>`;
    const d = r.data;
    if (!d) return `<div class="dim">Starting…</div>`;
    const pics = (d.paths || []).map((p) => `<a href="${img(p)}" target="_blank" rel="noopener"><img src="${img(p)}" alt="" loading="lazy"></a>`).join("");
    return `<div class="pj-row"><span class="pj-state">${esc(d.state || "")}</span><span class="dim">${esc(d.why || "")}</span></div>
      ${d.error ? `<div class="pj-err">${esc(d.error)}</div>` : ""}${pics ? `<div class="cp-grid">${pics}</div>` : ""}`;
  }

  function recent(pic) {
    const list = pic.recent || [];
    if (!list.length) return '<div class="dim">No pictures yet.</div>';
    return `<div class="cp-grid">${list.map((x) => `<figure class="cp-pic">
        <a href="${img(x.path)}" target="_blank" rel="noopener"><img src="${img(x.path)}" alt="${esc(x.prompt)}" loading="lazy"></a>
        <figcaption><div class="cp-said">${esc(x.prompt || "(no prompt)")}</div>
          <div class="dim">${esc(x.task || "by hand")} · <span title="${esc(x.run)}">${esc(ago(x.at))}</span></div></figcaption></figure>`).join("")}</div>`;
  }

  async function onTry(form) {
    const p = form.querySelector(".cp-prompt").value.trim();
    const args = [];
    if (p) args.push("--prompt", quote(p));
    for (const f of form.querySelectorAll("[data-input]")) {
      if (f.value.trim()) args.push("--" + f.dataset.input, quote(f.value.trim()));
    }
    clearTimeout(S.run && S.run.timer);
    S.run = { id: "", data: null };
    put(".cp-tryout", tryOut());
    try {
      S.run.id = (await api(url("/run"), { args: args.join(" ") })).run;
      poll(S.card.name, S.run.id);
    } catch (x) { S.run.err = x.message; }
    put(".cp-tryout", tryOut());
  }

  async function poll(name, id) {
    if (!S.card || S.card.name !== name || !S.run || S.run.id !== id) return;
    try { S.run.data = await api(url("/uses/" + encodeURIComponent(id))); } catch (x) { S.run.data = { state: "?", error: x.message, ended: true }; }
    if (!S.run || S.run.id !== id) return;
    put(".cp-tryout", tryOut());
    if (!ended(S.run.data)) S.run.timer = setTimeout(() => poll(name, id), 1000);
  }

  async function onChange(e) {
    const t = e.target;
    if (!S.card || !S.ctx) return;
    if (t.matches(".cp-out")) return S.ctx.save({ out: t.value.trim() });
    const wrap = t.closest("[data-tgt]");
    if (!wrap || !window.settingsTarget) return;
    const v = settingsTarget.value(wrap, t);
    const set = { target: v };
    if ("provider" in (S.card.spec || {})) {                  // the old form becomes a target card
      set.provider = null;
      if (!(S.card.spec || {}).out) set.out = `assets/generated/${S.card.name}/`;
    }
    return S.ctx.save(set);
  }

  function frame(el) {
    el.innerHTML = `<div class="cp">
      <div class="pp-label">Workflow</div><div class="cp-pick"></div>
      <div class="cp-wf"></div><div class="cp-outbox"></div>
      <h4 class="pp-h">Try</h4><div class="cp-tryform"></div><div class="cp-tryout"></div>
      <h4 class="pp-h">Last pictures <span class="dim">newest first</span></h4><div class="cp-recent"></div></div>`;
    S.last = {};
    if (!el.dataset.cpWired) {
      el.dataset.cpWired = "1";
      el.addEventListener("change", (e) => { if (e.target.closest(".cp-pick, .cp-outbox")) { e.stopPropagation(); onChange(e); } });
      el.addEventListener("submit", (e) => { if (e.target.matches(".cp-try")) { e.preventDefault(); e.stopPropagation(); onTry(e.target); } });
    }
  }

  function draw(el, card, pid, ro, ctx) {
    const pic = card.picture, title = el.previousElementSibling;
    if (title && title.matches && title.matches("h4.pp-h")) title.hidden = !pic;
    el.hidden = !pic;
    if (!pic) { el.innerHTML = ""; S.el = null; return; }
    const fresh = S.el !== el || !S.card || S.card.name !== card.name;
    if (fresh) { close(); Object.assign(S, { el }); frame(el); }
    Object.assign(S, { card, pid, ro: !!ro, ctx, editable: !!(ctx && ctx.editable) });
    loadModels();
    const w = pic.workflow;
    put(".cp-pick", picker(pic));
    put(".cp-wf", workflow(w));
    put(".cp-outbox", outPart(pic), true);
    put(".cp-tryform", tryForm(w), true);
    put(".cp-tryout", tryOut());
    put(".cp-recent", recent(pic));
  }

  function close() {
    if (S.run) clearTimeout(S.run.timer);
    Object.assign(S, { card: null, run: null, last: {} });
  }

  window.projectCardKinds.service = { title: "Pictures", keys: ["target", "out"], draw, close };
})();
