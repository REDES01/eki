// An agent card's sessions in its open card (projectcardpanel.js attaches it under "Sessions"): one
// line per session — when, the review's first line, tainted — newest first. A click puts the session
// in the card's place in the side panel (tainted, Review, Notes, Emotions, Screenshots, each shot
// fetched as a data URL and enlarged on click) under "← back to <card>", which brings the card back
// as it was. Routes: eki/api_cardsessions.py (…/sessions, …/sessions/<id>, …/sessions/<id>/file?name=).
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  // o: { api, url(tail), ago(t), name } of the open card; list/view/card: its boxes in the panel body
  const C = { o: null, list: null, view: null, card: null, sessions: null, sess: null, data: null, shots: {}, top: 0 };
  const shotName = (x) => typeof x === "string" ? x : x.name || x.path || "";
  const same = (name) => C.o && C.o.name === name;
  const scroller = () => C.card && C.card.parentNode;      // the panel body

  function listPart() {
    if (!C.sessions) return '<div class="dim">Loading…</div>';
    if (C.sessions.err) return `<div class="pj-err">${esc(C.sessions.err)}</div>`;
    if (!C.sessions.list.length) return '<div class="dim">No sessions yet.</div>';
    return C.sessions.list.map((s) => `<div class="pj-item pp-line${C.sess === s.id ? " on" : ""}" data-sess="${esc(s.id)}" role="button">
        <span class="dim pp-when">${esc(C.o.ago(s.when))}</span>
        <span class="grow pp-res">${esc((s.review || "").split("\n")[0] || "(no review)")}</span>
        ${s.tainted ? '<span class="pj-err" title="tainted">tainted</span>' : ""}</div>`).join("");
  }

  function sessionPart() {
    const d = C.data;
    const head = `<a href="#" class="cs-back" data-cs-back="1">← back to ${esc(C.o.name)}</a>
      <div class="pj-row"><b class="grow">${esc(C.sess)}</b>${d && d.when ? `<span class="dim">${esc(C.o.ago(d.when))}</span>` : ""}</div>`;
    if (!d) return head + '<div class="dim">Loading…</div>';
    if (d.err) return head + `<div class="pj-err">${esc(d.err)}</div>`;
    const shots = (d.screenshots || []).map(shotName);
    const notes = Array.isArray(d.notes) ? d.notes.join("\n") : d.notes;
    return `${head}${d.tainted ? `<div class="pj-err">tainted${d.taint ? ": " + esc(typeof d.taint === "string" ? d.taint : JSON.stringify(d.taint)) : ""}</div>` : ""}
      <h4 class="pp-h">Review</h4><pre class="pp-text">${esc(d.review || "(none)")}</pre>
      ${notes ? `<h4 class="pp-h">Notes</h4><pre class="pp-text">${esc(notes)}</pre>` : ""}
      ${(d.emotions || []).length ? `<h4 class="pp-h">Emotions</h4><div class="pp-tl">${d.emotions.map((m) =>
        `<div class="pp-tick"><span class="dim pp-t">${esc(m.t)}</span><b>${esc(m.emotion)}</b> <span class="dim">${esc(m.why || "")}</span></div>`).join("")}</div>` : ""}
      ${shots.length ? `<h4 class="pp-h">Screenshots</h4><div class="pj-grid pp-shots">${shots.map((n) =>
        `<a class="pp-shot" data-shot="${esc(n)}" title="${esc(n)}" href="#">${C.shots[n] ? `<img src="${esc(C.shots[n])}" alt="${esc(n)}">` : `<span class="dim">${esc(n)}</span>`}</a>`).join("")}</div>` : ""}`;
  }

  function paint() {
    if (C.list) C.list.innerHTML = listPart();
    if (C.view && C.sess) C.view.innerHTML = sessionPart();
  }

  async function load() {
    if (!C.o) return;
    const name = C.o.name;
    try {
      const got = await C.o.api(C.o.url("/sessions"));
      if (!same(name)) return;
      C.sessions = { list: got.sessions || [] };
    } catch (x) { if (same(name)) C.sessions = { list: [], err: x.message }; }
    paint();
  }

  // the session in the card's place: the card view hidden (what you typed in it stays), scrolled to the top
  async function open(id) {
    const name = C.o.name, body = scroller();
    C.top = body ? body.scrollTop : 0;
    Object.assign(C, { sess: id, data: null });
    if (C.card) C.card.hidden = true;
    if (C.view) C.view.hidden = false;
    if (body) body.scrollTop = 0;
    paint();
    try { C.data = await C.o.api(C.o.url("/sessions/" + encodeURIComponent(id))); } catch (x) { C.data = { err: x.message }; }
    if (!same(name) || C.sess !== id) return;
    paint();
    for (const n of (C.data.screenshots || []).map(shotName)) {
      if (!n || C.shots[n]) continue;
      try {
        const f = await C.o.api(C.o.url("/sessions/" + encodeURIComponent(id) + "/file?name=" + encodeURIComponent(n)));
        if (f.data_url) C.shots[n] = f.data_url;
      } catch (e) { /* the name stays as the thumbnail */ }
      if (same(name) && C.sess === id) paint();
    }
  }

  function back() {
    Object.assign(C, { sess: null, data: null });
    if (C.view) { C.view.hidden = true; C.view.innerHTML = ""; }
    if (C.card) C.card.hidden = false;
    const body = scroller();
    if (body) body.scrollTop = C.top;
    paint();
  }

  function onClick(e) {
    const t = e.target;
    if (t.closest("[data-cs-back]")) { e.preventDefault(); return back(); }
    const shot = t.closest("[data-shot]");
    if (shot) { e.preventDefault(); return shot.classList.toggle("big"); }
    const s = t.closest("[data-sess]");
    if (s && C.o) open(s.dataset.sess);
  }

  // a card opened (fresh state) or framed again (the same card, new boxes)
  function attach(list, view, card, o) {
    if (!same(o.name)) Object.assign(C, { sessions: null, sess: null, data: null, shots: {}, top: 0 });
    Object.assign(C, { o, list, view, card });
    for (const el of [list, view]) if (el) el.addEventListener("click", onClick);
    if (C.sess) { card.hidden = true; view.hidden = false; }
    paint();
    if (!C.sessions) load();
  }

  // the cards reloaded: the list again, unless a session is open
  function refresh() { if (C.o && C.sessions && !C.sess) load(); }

  function close() { Object.assign(C, { o: null, list: null, view: null, card: null, sessions: null, sess: null, data: null, shots: {} }); }

  window.cardSessions = { attach, refresh, close, back };
  if (window.projectCardPanel && projectCardPanel.redraw) projectCardPanel.redraw();
})();
