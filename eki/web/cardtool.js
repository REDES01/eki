// A Tool (service) card's own part in the side panel, under "What it runs": the command it runs and
// what comes out where (card.live.tool). An images (ComfyUI) card also shows the graphs it can run —
// title, model, default size, a line on each, the one `eki use <name>` uses — and lets you change the
// default graph (a choicemenu.js dropdown), the output folder and the size; whether ComfyUI is up and
// which model it last loaded; and the last few pictures as thumbnails, each opening the full picture.
// Settings saved with ctx.save({key: value}); ctx.problems shows beside the field. Read-only with ro or
// a pool card. The card JSON comes from the server (eki/api_cards.py); a card without live shows what it has.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const S = { box: null, card: null, ctx: null, off: true, last: "" };

  if (document.querySelector && !document.querySelector('link[href="/ui/cardtool.css"]')) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/ui/cardtool.css";
    document.head.appendChild(l);
  }

  const live = () => (S.card && S.card.live) || {};
  const spec = () => (S.card && S.card.spec) || {};
  const problem = (key) => {
    const p = S.ctx && S.ctx.problems ? S.ctx.problems[key] : "";
    return p ? `<div class="pj-err ct-problem" data-ct-problem="${esc(key)}">${esc(p)}</div>` : "";
  };

  function toolPart() {
    const t = live().tool || {};
    const runs = t.command ? `<div class="ct-line"><span class="ct-label">Runs:</span> <code class="ct-cmd">${esc(t.command)}</code></div>` : "";
    const outs = t.outputs ? `<div class="ct-line"><span class="ct-label">Results:</span> ${esc(t.outputs)}</div>` : "";
    return runs || outs ? `<div class="ct-tool">${runs}${outs}</div>` : "";
  }

  function graphsTable(gs) {
    const name = S.card.name;
    const rows = gs.map((g) => `<tr class="ct-graph${g.default ? " ct-default" : ""}" data-row="${esc(g.row)}">
      <td><b>${esc(g.title || g.label || g.row)}</b>${g.default ? ` <span class="ct-mark">used by <code>eki use ${esc(name)}</code></span>` : ""}
        ${g.explain ? `<div class="dim ct-explain">${esc(g.explain)}</div>` : ""}</td>
      <td class="ct-model">${esc(g.model || "")}</td><td class="ct-size">${esc(g.size || "")}</td></tr>`).join("");
    return `<table class="ct-graphs"><thead><tr><th>Graph</th><th>Model</th><th>Default size</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function graphChoice(gs) {
    const l = live(), value = spec().graph != null ? spec().graph : l.default;
    const cs = gs.map((g) => g.row), help = {};
    for (const g of gs) help[g.row] = (g.title ? g.title + " — " : "") + (g.explain || "");
    const menu = window.choiceMenu ? choiceMenu.html("graph", cs, value, help, S.off)
      : `<code>${esc(value)}</code>`;
    return `<div class="ct-field"><label class="ct-label">Default graph</label>${menu}
      <div class="dim ct-note">What <code>eki use ${esc(S.card.name)}</code> runs when it isn't told which.</div>${problem("graph")}</div>`;
  }

  function textField(key, label, value, extra, note) {
    return `<div class="ct-field"><label class="ct-label">${esc(label)}</label>
      <input class="ct-in" data-ct="${esc(key)}" ${extra} value="${esc(value == null ? "" : value)}"${S.off ? " disabled" : ""}>
      ${note || ""}${problem(key)}</div>`;
  }

  function settingsPart() {
    const l = live(), s = spec();
    const out = s.out != null ? s.out : l.out;
    const where = l.out_path ? `<div class="dim ct-note">Pictures are saved in <code>${esc(l.out_path)}</code></div>` : "";
    const size = (k) => s[k] != null ? s[k] : "";
    const dflt = (l.graphs || []).find((g) => g.default) || {};
    const ph = String(dflt.size || "").split(/[x×]/).map((x) => x.trim());
    return textField("out", "Output folder", out, 'type="text" spellcheck="false"', where)
      + `<div class="ct-size-row">`
      + textField("width", "Width", size("width"), `type="number" min="64" step="8" placeholder="${esc(ph[0] || "graph's own")}"`)
      + textField("height", "Height", size("height"), `type="number" min="64" step="8" placeholder="${esc(ph[1] || "graph's own")}"`)
      + `</div><div class="dim ct-note">Leave the size empty to use each graph's own.</div>`;
  }

  function comfyPart() {
    const c = live().comfy;
    if (!c) return "";
    if (c.up) return `<div class="ct-line ct-up">ComfyUI: up at <code>${esc(c.url)}</code>${c.model ? ` · last model <code>${esc(c.model)}</code>` : ""}</div>`;
    return '<div class="ct-line dim ct-down">ComfyUI: not running — starts on the next picture</div>';
  }

  function recentPart() {
    const r = live().recent || [];
    if (!r.length) return live().graphs ? '<div class="dim ct-note">No pictures yet.</div>' : "";
    return `<div class="ct-label">Last pictures</div><div class="ct-thumbs">${r.map((p) =>
      `<a class="ct-thumb" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(p.path)}">
        <img src="${esc(p.url)}" alt="${esc(p.path)}" loading="lazy"><span class="dim">${esc(p.when || "")}</span></a>`).join("")}</div>`;
  }

  function html() {
    const gs = live().graphs;
    let h = toolPart();
    if (Array.isArray(gs) && gs.length) {
      h += `<div class="ct-label">Graphs it can run</div>${graphsTable(gs)}${graphChoice(gs)}${settingsPart()}${comfyPart()}${recentPart()}`;
    } else h += comfyPart() + recentPart();
    return h || '<div class="dim">Nothing more to show for this tool yet.</div>';
  }

  function paint(force) {
    if (!S.box || !S.card) return;
    const a = document.activeElement;
    const typing = a && S.box.contains && S.box.contains(a) && a.matches && a.matches("input");
    const h = html();
    if (!force && (typing || S.last === h)) return;
    S.box.innerHTML = h; S.last = h;
  }

  function save(set) {
    if (S.off || !S.ctx || !S.ctx.save) return;
    const p = S.ctx.save(set);
    if (p && p.then) p.then(() => paint(true), () => paint(true));
  }

  function onChange(e) {
    const t = e.target, key = t && t.dataset && t.dataset.ct;
    if (!key) return;
    const v = String(t.value == null ? "" : t.value).trim();
    if (key === "out") save({ out: v });
    else save({ [key]: v === "" ? null : Number(v) });
  }

  // the panel wires its own dropdowns on the whole card; ours is handled here and goes no further
  const mine = (e) => e.target && e.target.closest && e.target.closest(".cm") && S.box && S.box.contains(e.target);
  function stop(e) { if (mine(e)) e.stopPropagation(); }

  function wire(el) {
    if (el.dataset.ctWired) return;
    el.dataset.ctWired = "1";
    if (window.choiceMenu) choiceMenu.attach(el, (key, value) => save({ [key]: value }));
    el.addEventListener("click", stop);
    el.addEventListener("keydown", stop);
    el.addEventListener("change", onChange);
  }

  window.projectCardKinds = window.projectCardKinds || {};
  window.projectCardKinds["service"] = {
    title: "What it runs",
    keys: ["graph", "out", "width", "height"],
    draw(el, card, pid, ro, ctx) {
      if (!el || !card) return;
      const off = !!ro || !!card.ref || !!card.pool || (ctx && ctx.editable === false);
      if (el !== S.box) S.last = "";
      Object.assign(S, { box: el, card, ctx: ctx || null, off });
      wire(el);
      paint(false);
    },
    close() { Object.assign(S, { box: null, card: null, ctx: null, off: true, last: "" }); },
  };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
