// Station: what `eki self`, `eki builds`, `eki route`, `eki observe` and the digest show, and
// the self actions. It takes #main's place while the address is #station (nav.js) and reloads
// every 5 s while shown. The page decides nothing: each button POSTs to the function the CLI
// calls (eki/api_station.py) and shows the server's error beside it.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const KINDS = ["fault", "handoff", "correction", "limit", "run", "regression"];   // observe.KINDS
  const ENDS = ["dropped", "applied", "landed", "live"];                            // nothing left to drop
  const RETRY = ["left", "unfit", "dropped", "rolled back"];
  const S = { on: false, timer: null, busy: false, errs: {}, open: new Set(), asks: "" };

  function ago(t) {
    if (!t) return "-";
    const s = Math.floor(Date.now() / 1000 - t);
    for (const [u, n] of [["d", 86400], ["h", 3600], ["m", 60]]) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
    return `${s}s ago`;
  }
  const clock = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const put = (id, html) => { const el = $(id); if (el && el.innerHTML !== html) el.innerHTML = html; };
  const tlink = (t, label) => t ? ` <a class="st-link" href="#${encodeURIComponent(t)}">${label || "thread"}</a>` : "";
  const err = (key) => S.errs[key] ? `<span class="st-err">${esc(S.errs[key])}</span>` : "";
  const btn = (act, label, data, extra) => `<button type="button" class="st-btn${extra ? " " + extra : ""}" data-act="${act}"${data ? ` data-id="${esc(data)}"` : ""}>${label}</button>`;

  function box() {
    let el = $("station-view");
    if (el) return el;
    el = document.createElement("div");
    el.id = "station-view";
    const sect = (id, title, extra) => `<section class="st-sect" id="st-${id}"><h3>${title}${extra || ""}</h3><div id="st-${id}-body"></div></section>`;
    el.innerHTML = `<div class="st-col">
      <section class="st-sect"><h3>Self</h3>
        <form id="st-wish" class="st-wish" autocomplete="off">
          <textarea id="st-wish-text" rows="2" placeholder="A change to eki, in your words…"></textarea>
          <div class="st-row"><label class="st-check" title="Plan it as written, without drafting it first"><input type="checkbox" id="st-asis"> as-is</label>
            <span class="grow"></span><span id="st-wish-err"></span><button type="submit" class="st-btn primary">Ask eki to build it</button></div>
        </form>
        <div id="st-self-body"></div></section>
      ${sect("asks", "Open questions")}
      ${sect("builds", "Builds")}
      <section class="st-sect"><h3>Routing</h3>
        <form id="st-try" class="st-row" autocomplete="off"><input id="st-try-q" placeholder="Try a request: where would it go?">
          <button type="submit" class="st-btn">Explain</button></form>
        <div id="st-try-out"></div><div id="st-route-body"></div></section>
      <section class="st-sect"><h3>Journal
        <select id="st-since"><option value="24h">24h</option><option value="7d">7d</option><option value="30d">30d</option></select>
        <select id="st-kind"><option value="all">all but runs</option>${KINDS.map((k) => `<option>${k}</option>`).join("")}</select></h3>
        <div id="st-journal-body"></div></section>
      ${sect("digest", "Digest", ` <span class="grow"></span><span id="st-digest-err"></span>${btn("digest", "Write now")}`)}
    </div>`;
    $("main").insertBefore(el, $("composer"));
    el.addEventListener("click", onClick);
    $("st-wish").addEventListener("submit", wish);
    $("st-try").addEventListener("submit", explain);
    $("st-since").addEventListener("change", () => journal());
    $("st-kind").addEventListener("change", () => journal());
    return el;
  }

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" }, body: JSON.stringify(body) });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    return got;
  }

  // ---- what it shows ------------------------------------------------------------------------

  function self(s) {
    const top = `<div class="st-line">autonomy <b>${esc(s.autonomy)}</b>
        ${btn("autonomy", s.autonomy === "apply" ? "switch to propose" : "switch to apply", s.autonomy === "apply" ? "propose" : "apply", "small")}
        · ${esc(s.parallel)} at once · source <code>${esc(s.source)}</code>
        <span class="grow"></span>${err("release")}${btn("release", "Release a train now", "", "small")}${err("autonomy")}</div>`;
    const queue = s.queue.length ? `<div class="st-sub">queue</div>` + s.queue.map((q) => `<div class="st-q">
        <span class="st-pos">${q.pos ? q.pos + "." : "-"}</span><code>${esc(q.id)}</code>
        <span class="dim">on ${esc((q.head || "").slice(0, 8) || "-")}</span>
        <span class="st-stage">${esc(q.stage)}</span><span class="st-t">${esc(q.title)}</span>
        ${q.docs_only ? '<span class="st-tag">docs only</span>' : ""}${tlink(q.thread)}</div>`).join("") : "";
    const goals = s.goals.length ? s.goals.map(goal).join("") :
      '<div class="dim">nothing yet — the box above asks for a change</div>';
    return top + queue + goals;
  }

  function goal(g) {
    return `<div class="st-goal"><div class="st-ghead"><code>${esc(g.id)}</code>
        <span class="st-state s-${esc(g.state.replace(/\s/g, "-"))}">${esc(g.state)}</span>
        <span class="dim">${ago(g.created_at)}</span><span class="st-t" title="${esc(g.text)}">${esc(g.wish)}</span>${tlink(g.thread)}</div>
      ${g.drafting ? `<div class="st-note">${esc(g.drafting).replace(/\n\s*/g, "<br>")}</div>` : ""}
      ${g.error ? `<div class="st-note bad">! ${esc(g.error.slice(0, 200))}</div>` : ""}
      ${g.items.map(item).join("")}</div>`;
  }

  function item(it) {
    const acts = (["proposed", "locked"].includes(it.state) ? btn("apply", "apply", it.id, it.state === "locked" ? "warn" : "") : "") +
      (RETRY.includes(it.state) ? btn("retry", "retry", it.id) : "") +
      (!ENDS.includes(it.state) ? btn("drop", "drop", it.id) : "");
    return `<div class="st-item" data-state="${esc(it.state)}"><div class="st-ihead"><code>${esc(it.id)}</code>
        <span class="st-state s-${esc(it.state.replace(/\s/g, "-"))}">${esc(it.state)}</span>
        <span class="dim st-where">${esc(it.where)}</span><span class="st-t">${esc(it.title)}</span>
        ${it.docs_only ? '<span class="st-tag">docs only</span>' : ""}${tlink(it.thread)}
        <span class="grow"></span>${err(it.id)}<span class="st-acts" data-state="${esc(it.state)}">${acts}</span></div>
      ${it.note ? `<div class="st-note">${esc(it.note)}</div>` : ""}</div>`;
  }

  function builds(b) {
    const rows = b.builds.map((x) => `<tr class="${x.current ? "cur" : ""}"><td>${x.current ? "→" : x.previous ? "↩" : ""}</td>
        <td><code>${esc(x.id)}</code></td><td><code>${esc((x.commit || "").slice(0, 12))}</code></td>
        <td class="dim">${ago(x.made_at)}</td><td>${x.healthy ? "healthy" : "-"}</td><td>${esc(x.check)}</td>
        <td class="v-${esc(x.verdict)}">${esc(x.verdict)}</td>
        <td>${x.verdict === "worse" ? err("undo:" + x.id) + btn("undo", "undo", x.id, "warn") : ""}</td>
        <td class="dim st-path">${esc(x.path)}</td></tr>`).join("");
    const sw = b.swap, rb = b.rollback;
    const lines = [`running: <code>${esc(b.running)}</code>`];
    if (sw) lines.push(`last swap: ${esc(sw.state)} → ${esc(sw.target)} (${ago(sw.at)}; ${esc(sw.why || "")})`);
    if (rb && (!sw || (rb.at || 0) >= (sw.at || 0)))
      lines.push(`rolled back ${esc(clock(rb.at))}: ${esc(rb.from)} exited ${esc(rb.exit)} after ${esc(rb.after)}s → ${esc(rb.to)}`);
    return `<div class="st-line">${lines[0]}</div>` +
      (rows ? `<table class="st-table">${rows}</table>` : '<div class="dim">no builds yet</div>') +
      lines.slice(1).map((l) => `<div class="st-line">${l}</div>`).join("");
  }

  const target = (t) => `<div class="st-target ${t.ok ? "ok" : "no"}">${t.ok ? "✓" : "✗"} <b>${esc(t.name)}</b>
      ${t.can ? `<span class="dim">[${esc(t.can.join(", "))}]</span>` : ""}${t.why ? ` <span class="dim">(${esc(t.why)})</span>` : ""}</div>`;

  function table(r) {
    return `<div class="st-line dim">checker: ${esc(r.checker)}</div>` + r.rows.map((row) => `<div class="st-rrow">
        <div><b>${esc(row.key)}</b> ${esc(row.title || "")} <span class="dim">needs: [${esc((row.needs || []).join(", "))}]</span></div>
        <div class="st-targets">${(row.targets || []).map((t) => `<span class="st-chip">${esc(t)}</span>`).join("")}</div></div>`).join("");
  }

  function entry(e, i) {
    const tb = e.kind === "fault" && e.data && e.data.traceback;
    const key = `${e.t}:${e.kind}:${i}`;
    return `<div class="st-entry k-${esc(e.kind)}${tb ? " can-open" : ""}" data-key="${esc(key)}">
        <span class="dim">${esc(clock(e.t))}</span><span class="st-kind">${esc(e.kind)}</span>
        <code>${esc(e.run_id || "-")}</code><span class="dim">${esc(e.provider || "-")}</span><span class="st-t">${esc(e.summary)}</span>
        ${tb && S.open.has(key) ? `<pre class="st-tb">${esc(tb)}</pre>` : ""}</div>`;
  }

  function digests(d) {
    const list = d.pages.map((p, i) => `<a class="st-chip" href="/api/file?path=${encodeURIComponent(p)}" target="_blank" rel="noopener">${esc(p.split("/").pop().replace(/\.md$/, ""))}${i ? "" : " · latest"}</a>`).join("");
    return (list ? `<div class="st-targets">${list}</div>` : '<div class="dim">no digest yet</div>') +
      (d.latest ? `<div class="st-md">${md.render(d.latest)}</div>` : "");
  }

  async function part(id, path, fn) {
    try { put(`st-${id}-body`, fn(await api(path))); }
    catch (e) { put(`st-${id}-body`, `<div class="st-err">Can't load: ${esc(e.message)}</div>`); }
  }

  function journal() {
    return part("journal", `/api/journal?since=${$("st-since").value}&kind=${$("st-kind").value}`,
      (j) => j.entries.length ? j.entries.map(entry).join("") : '<div class="dim">nothing in this span</div>');
  }

  async function load() {
    if (!S.on || S.busy) return;
    S.busy = true;
    try {
      await Promise.all([
        part("self", "/api/station", (st) => {
          const asks = JSON.stringify(st.asks);
          if (asks !== S.asks) {   // re-rendered only when they change, so a half-picked answer stays
            S.asks = asks;
            put("st-asks-body", st.asks.length ? st.asks.map(cards.render).join("") : '<div class="dim">none — nothing is waiting on you</div>');
          }
          $("meta").textContent = `autonomy ${st.self.autonomy} · ${st.self.queue.length} in line · ${st.asks.length} open question${st.asks.length === 1 ? "" : "s"}`;
          return self(st.self);
        }),
        part("builds", "/api/builds", builds),
        part("route", "/api/route", table),
        journal(),
        part("digest", "/api/digests", digests),
      ]);
    } finally { S.busy = false; }
  }

  // ---- what it does --------------------------------------------------------------------------

  async function act(key, path, body) {
    delete S.errs[key];
    try { await api(path, body || {}); }
    catch (e) { S.errs[key] = e.message; }
    await load();
  }

  function onClick(e) {
    const entryEl = e.target.closest(".st-entry.can-open");
    if (entryEl && !e.target.closest("a, pre")) {
      const k = entryEl.dataset.key;
      S.open.has(k) ? S.open.delete(k) : S.open.add(k);
      return journal();
    }
    const b = e.target.closest("[data-act]");
    if (!b || b.closest(".ask")) return;
    const id = b.dataset.id, a = b.dataset.act;
    if (a === "apply") {
      const locked = b.closest(".st-acts").dataset.state === "locked";
      if (locked && !confirm(`Item ${id} touches locked files. Queue it anyway?`)) return;
      return act(id, `/api/self/items/${encodeURIComponent(id)}/apply`, locked ? { yes: true } : {});
    }
    if (a === "drop" && !confirm(`Drop item ${id}?`)) return;
    if (a === "drop" || a === "retry") return act(id, `/api/self/items/${encodeURIComponent(id)}/${a}`);
    if (a === "release") return act("release", "/api/self/release");
    if (a === "autonomy") return act("autonomy", "/api/self/autonomy", { mode: id });
    if (a === "undo" && confirm(`Undo build ${id}? eki plans a goal to take its changes back.`))
      return act("undo:" + id, `/api/builds/${encodeURIComponent(id)}/undo`);
    if (a === "digest") {
      b.disabled = true;
      api("/api/digests/write", {}).then(() => put("st-digest-err", ""), (x) => put("st-digest-err", `<span class="st-err">${esc(x.message)}</span>`))
        .then(load).finally(() => { b.disabled = false; });
    }
  }

  async function wish(e) {
    e.preventDefault();
    const text = $("st-wish-text").value.trim();
    if (!text) return;
    put("st-wish-err", "");
    try {
      const got = await api("/api/self", { text, as_is: $("st-asis").checked });
      $("st-wish-text").value = "";
      put("st-wish-err", `<span class="dim">goal ${esc(got.goal)}: ${esc(got.state)}</span>`);
    } catch (x) { put("st-wish-err", `<span class="st-err">${esc(x.message)}</span>`); }
    load();
  }

  async function explain(e) {
    e.preventDefault();
    const q = $("st-try-q").value.trim();
    if (!q) return put("st-try-out", "");
    try {
      const x = await api("/api/route?q=" + encodeURIComponent(q));
      put("st-try-out", `<div class="st-explain"><div><span class="dim">why:</span> ${esc(x.why)}</div>
        <div><span class="dim">row:</span> <b>${esc(x.row)}</b> — ${esc(x.title)} <span class="dim">needs: [${esc(x.needs.join(", "))}]</span></div>
        ${x.targets.map(target).join("")}</div>`);
    } catch (x) { put("st-try-out", `<div class="st-err">${esc(x.message)}</div>`); }
  }

  function show() {
    S.on = true;
    box().hidden = false;
    $("title").textContent = "Station";
    $("meta").textContent = "";
    S.asks = "";
    load();
    clearInterval(S.timer);
    S.timer = setInterval(load, 5000);
  }

  function hide() {
    if (!S.on) return;
    S.on = false;
    clearInterval(S.timer);
    S.timer = null;
    box().hidden = true;
  }

  window.addEventListener("eki:refresh", () => { if (S.on) { S.asks = ""; load(); } });
  window.station = { show, hide, on: () => S.on };
  nav.register("station", { show, hide });
})();
