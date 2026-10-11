// The Cards area's type filter (projectcards.js draws it in the header row): a button that opens a list of
// "All types" and each kind present with its count. The grid is always sorted by kind, then name; picking a
// kind leaves only its cards. The choice is kept per project in this viewer's localStorage
// (eki.cards.type.<project id>); a kept kind no longer present shows as All types. It never closes a card.
(function () {
  const esc = (s) => md.esc(String(s == null ? "" : s));
  const kindOf = (c) => c.kind || "?";
  const slot = (pid) => "eki.cards.type." + pid;

  function remembered(pid) {
    try { return localStorage.getItem(slot(pid)) || ""; } catch (e) { return ""; }
  }
  function remember(pid, kind) {
    try { if (kind) localStorage.setItem(slot(pid), kind); else localStorage.removeItem(slot(pid)); } catch (e) {}
  }

  // [[kind, n], …] for the kinds present, by name
  function counts(cards) {
    const n = {};
    for (const c of cards || []) n[kindOf(c)] = (n[kindOf(c)] || 0) + 1;
    return Object.keys(n).sort().map((k) => [k, n[k]]);
  }

  function sorted(cards) {
    const by = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    return (cards || []).slice().sort((a, b) => by(kindOf(a), kindOf(b)) || by(String(a.name), String(b.name)));
  }

  // one filter per mounted project: its kind ("" for all), whether the list is open, the highlighted row
  function state(pid) { return { pid, kind: remembered(pid), open: false, active: 0 }; }

  // the kind in effect: the chosen one while it is present
  const current = (F, cards) => (F.kind && counts(cards).some(([k]) => k === F.kind) ? F.kind : "");
  const choices = (cards) => [["", 0], ...counts(cards)];

  function shown(F, cards) {
    const k = current(F, cards);
    return sorted(cards).filter((c) => !k || kindOf(c) === k);
  }

  function html(F, cards) {
    const k = current(F, cards), list = choices(cards);
    const rows = F.open ? `<ul class="pc-flist" role="listbox" aria-label="Card type">${list.map(([kind, n], i) =>
      `<li class="pc-fopt${i === F.active ? " active" : ""}${kind === k ? " on" : ""}" role="option"
        aria-selected="${kind === k}" data-fkind="${esc(kind)}">${kind ? `${esc(kind)} <span class="dim">${n}</span>`
        : "All types"}</li>`).join("")}</ul>` : "";
    return `<button type="button" class="pc-fbtn" data-filter="toggle" aria-haspopup="listbox"
      aria-expanded="${F.open}">${k ? esc(k) : "All types"} <span class="pc-fcaret">▾</span></button>${rows}`;
  }

  function pick(F, kind) {
    F.kind = kind; F.open = false;
    remember(F.pid, kind);
  }

  function toggle(F, cards) {
    F.open = !F.open;
    if (F.open) F.active = Math.max(0, choices(cards).findIndex(([k]) => k === current(F, cards)));
  }

  // a click in the area: true when it was the filter's (the caller redraws); one elsewhere closes the list
  // and goes on to what it was on
  function click(F, target, cards) {
    const opt = target.closest("[data-fkind]");
    if (opt) { pick(F, opt.dataset.fkind); return true; }
    if (target.closest("[data-filter]")) { toggle(F, cards); return true; }
    F.open = false;
    return false;
  }

  // a key on the button: arrows open and move, Enter picks, Esc closes; true when it was the filter's
  function key(F, e, cards) {
    if (!e.target || !e.target.closest || !e.target.closest("[data-filter]")) return false;
    const n = choices(cards).length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!F.open) toggle(F, cards);
      else F.active = (F.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
    } else if (e.key === "Enter" || e.key === " ") {
      if (F.open) pick(F, choices(cards)[Math.min(F.active, n - 1)][0]); else toggle(F, cards);
    } else if (e.key === "Escape") {
      if (!F.open) return false;
      F.open = false;
    } else return false;
    e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();   // Esc closes the list, not the side panel
    return true;
  }

  window.projectCardFilter = { state, counts, sorted, shown, html, pick, click, key, remembered };
})();
