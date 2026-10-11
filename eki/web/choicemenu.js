// A small dropdown for a setting with fixed choices (projectcardpanel.js draws one per card.choices key):
// a button showing the current value, a list of the choices, and each choice's one-line explanation
// (card.choice_help, from its kind) shown in the open list beside the hovered or highlighted option; the
// closed button shows just the value. A native <select> can't explain its options in WKWebView, hence this.
//   html(key, choices, value, help, disabled) -> the markup; help is {value as string: text}, may be missing
//   attach(rootEl, onPick(key, value))        -> wires every dropdown drawn inside rootEl, once
// Keyboard: Enter, Space or ArrowDown opens; ArrowUp/ArrowDown move; Home/End jump; Enter or Space picks;
// Esc closes and refocuses the button; Tab closes. A value
// picked from true/false choices comes back as a real boolean.
(function () {
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const str = (v) => v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  const id = (key) => "cm-" + String(key).replace(/[^\w-]/g, "_");

  function html(key, choices, value, help, disabled) {
    const h = help || {}, cs = choices || [], n = id(key);
    const bools = cs.length > 0 && cs.every((c) => typeof c === "boolean");
    const bad = !cs.some((c) => c === value), shown = str(value);
    const opts = cs.map((c, i) => {
      const t = h[str(c)], on = !bad && c === value;
      return `<li class="cm-opt${on ? " on" : ""}" role="option" id="${n}-${i}" data-value="${esc(str(c))}" aria-selected="${on}"><span class="cm-name">${esc(str(c))}</span>${t ? `<span class="cm-help">${esc(t)}</span>` : ""}</li>`;
    }).join("");
    return `<div class="cm"><button type="button" class="set-in cm-btn${bad ? " invalid" : ""}" data-key="${esc(key)}"
      data-type="${bools ? "boolchoice" : "choice"}" data-value="${esc(shown)}" aria-haspopup="listbox" aria-expanded="false"
      aria-controls="${n}"${disabled ? " disabled" : ""}><span class="cm-val">${esc(shown)}</span><span class="cm-caret" aria-hidden="true">▾</span></button><ul class="cm-list" role="listbox" id="${n}" tabindex="-1" aria-label="${esc(key)}" hidden>${opts}</ul></div>`;
  }

  // ---- behaviour ------------------------------------------------------------------------------

  const parts = (wrap) => ({ btn: wrap.querySelector(".cm-btn"), list: wrap.querySelector(".cm-list"),
                             opts: Array.from(wrap.querySelectorAll(".cm-opt")) });
  const isOpen = (wrap) => wrap.classList.contains("open");

  function highlight(wrap, i) {
    const { list, opts } = parts(wrap);
    if (!opts.length) return;
    i = Math.max(0, Math.min(opts.length - 1, i));
    opts.forEach((o, j) => o.classList.toggle("hi", j === i));
    list.setAttribute("aria-activedescendant", opts[i].id);
    if (opts[i].scrollIntoView) opts[i].scrollIntoView({ block: "nearest" });
  }
  const current = (wrap) => parts(wrap).opts.findIndex((o) => o.classList.contains("hi"));

  function open(wrap) {
    const { btn, list, opts } = parts(wrap);
    if (!btn || btn.disabled || isOpen(wrap)) return;
    wrap.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    list.hidden = false;
    const at = opts.findIndex((o) => o.classList.contains("on"));
    highlight(wrap, at < 0 ? 0 : at);
    list.focus();
  }

  function close(wrap, refocus) {
    if (!isOpen(wrap)) return;
    const { btn, list, opts } = parts(wrap);
    wrap.classList.remove("open");
    btn.setAttribute("aria-expanded", "false");
    list.hidden = true;
    list.removeAttribute("aria-activedescendant");
    opts.forEach((o) => o.classList.remove("hi"));
    if (refocus) btn.focus();
  }

  function pick(wrap, opt, onPick) {
    const { btn } = parts(wrap);
    const raw = opt.dataset.value;
    const value = btn.dataset.type === "boolchoice" && (raw === "true" || raw === "false") ? raw === "true" : raw;
    close(wrap, true);
    if (raw !== btn.dataset.value || btn.classList.contains("invalid")) onPick(btn.dataset.key, value);
  }

  function attach(root, onPick) {
    root.addEventListener("click", (e) => {
      const t = e.target, wrap = t.closest && t.closest(".cm");
      for (const w of root.querySelectorAll(".cm.open")) if (w !== wrap) close(w);
      if (!wrap) return;
      const opt = t.closest(".cm-opt");
      if (opt) return isOpen(wrap) && pick(wrap, opt, onPick);
      if (t.closest(".cm-btn")) isOpen(wrap) ? close(wrap, true) : open(wrap);
    });
    root.addEventListener("mouseover", (e) => {
      const opt = e.target.closest && e.target.closest(".cm-opt"), wrap = opt && opt.closest(".cm");
      if (wrap && isOpen(wrap)) highlight(wrap, parts(wrap).opts.indexOf(opt));
    });
    root.addEventListener("keydown", (e) => {
      const wrap = e.target.closest && e.target.closest(".cm");
      if (!wrap) return;
      const k = e.key;
      if (!isOpen(wrap)) {
        if ((k === "Enter" || k === " " || k === "ArrowDown") && e.target.closest(".cm-btn")) { e.preventDefault(); open(wrap); }
        return;
      }
      if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); highlight(wrap, current(wrap) + (k === "ArrowDown" ? 1 : -1)); }
      else if (k === "Home" || k === "End") { e.preventDefault(); highlight(wrap, k === "Home" ? 0 : 1e9); }
      else if (k === "Enter" || k === " ") {
        e.preventDefault();
        const o = parts(wrap).opts[current(wrap)];
        if (o) pick(wrap, o, onPick);
      } else if (k === "Escape") { e.preventDefault(); e.stopPropagation(); close(wrap, true); }
      else if (k === "Tab") close(wrap);
    });
    // a click anywhere else closes what is open
    if (document.addEventListener) {
      document.addEventListener("click", (e) => {
        for (const w of root.querySelectorAll(".cm.open")) if (!w.contains(e.target)) close(w);
      });
    }
  }

  window.choiceMenu = { html, attach };
})();
