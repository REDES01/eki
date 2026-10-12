// Pictures that shouldn't be on screen: every <img> showing /api/file?path=<p> whose <p> is marked
// sensitive (GET /api/sensitive, eki/api_sensitive.py) is blurred under a "Sensitive · click to
// show" label, wherever it is drawn — threads, the project page, card panels, Artifacts, the side
// panel — by watching the document, so none of those files need to know. The first click on a
// blurred picture shows it for this page view only and goes no further (no link, no panel).
// Right-click any such picture to mark it or take the mark off (POST /api/sensitive). Whether to
// blur at all is the person's own preference in this window: localStorage eki.blurSensitive, '0'
// is off, anything else (or nothing) is on. Settings → Pictures shows it; this file registers it.
(function () {
  const KEY = "eki.blurSensitive";
  const S = { paths: new Set(), shown: new Set(), menu: null, queued: false };

  const store = () => { try { return window.localStorage || null; } catch (e) { return null; } };
  function blurOn() { const s = store(); return !s || s.getItem(KEY) !== "0"; }
  function setBlur(on) { const s = store(); if (s) s.setItem(KEY, on ? "1" : "0"); apply(); }

  // the picture's path when src is /api/file?path=<p> (extra params, like panel.js's &t=, are fine)
  function pathOf(img) {
    const src = (img && img.getAttribute && img.getAttribute("src")) || "";
    const m = /^(?:https?:\/\/[^/]+)?\/api\/file\?(.*)$/.exec(src);
    const p = m ? new URLSearchParams(m[1]).get("path") : null;
    return p && p.replace(/\/(\.\/)+/g, "/").replace(/\/{2,}/g, "/");   // as the server keeps it (normpath)
  }

  const is = (p) => !!p && S.paths.has(p);

  async function load() {
    try {
      const r = await fetch("/api/sensitive");
      const got = await r.json();
      if (r.ok) S.paths = new Set(got.paths || []);
    } catch (e) { /* the engine is away: keep what we had */ }
    apply();
    return S.paths;
  }

  async function mark(path, on) {
    const r = await fetch("/api/sensitive", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eki": "1" },
      body: JSON.stringify({ path, sensitive: !!on }),
    });
    const got = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(got.error || r.statusText);
    if (!on) S.shown.delete(path);
    await load();
    return got;
  }

  // the element the label sits on: the img's parent when the img is all it holds (a tile, a
  // button), else a small box put around the img (a picture in the middle of a message)
  function host(img) {
    const p = img.parentElement;
    if (!p) return null;
    if (p.classList.contains("sens-box")) return p;
    const own = p.children.length === 1 || p.tagName === "BUTTON" || p.tagName === "A";
    if (own && p.querySelectorAll("img").length === 1) return p;
    const box = document.createElement("span");
    box.className = "sens-box";
    p.insertBefore(box, img);
    box.appendChild(img);
    return box;
  }

  function one(img) {
    const p = pathOf(img);
    if (p == null) return;
    const want = blurOn() && is(p) && !S.shown.has(p);
    if (want === img.classList.contains("sens-blur")) return;
    img.classList.toggle("sens-blur", want);
    const h = want ? host(img) : img.parentElement;
    if (!h) return;
    h.classList.toggle("sens-on", want);
    if (want && window.getComputedStyle && getComputedStyle(h).position === "static") h.classList.add("sens-rel");
  }

  function apply() { document.querySelectorAll("img").forEach(one); }

  // many mutations come at once (a thread drawn with innerHTML): one pass after them
  function soon() {
    if (S.queued) return;
    S.queued = true;
    Promise.resolve().then(() => { S.queued = false; apply(); });
  }

  // capture, before any page's own click: the first click only reveals
  window.addEventListener("click", (e) => {
    if (S.menu && !S.menu.contains(e.target)) close();
    const img = e.target && e.target.closest && e.target.closest("img.sens-blur");
    if (!img) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
    S.shown.add(pathOf(img));
    apply();
  }, true);

  // ---- the small menu: Mark sensitive / Not sensitive ----------------------------------------

  function close() { if (S.menu) { S.menu.remove(); S.menu = null; } }

  // menu(path, x, y, after): opens at x, y; after(on) runs once the mark is written
  function menu(path, x, y, after) {
    close();
    const on = is(path);
    const m = document.createElement("div");
    m.className = "sens-menu";
    m.innerHTML = `<button type="button" class="sens-item">${on ? "Not sensitive" : "Mark sensitive"}</button>`;
    m.style.left = x + "px";
    m.style.top = y + "px";
    m.addEventListener("click", async (e) => {
      e.stopPropagation();
      close();
      try { await mark(path, !on); if (after) after(!on); }
      catch (err) { alert("Can't change the mark: " + err.message); }
    });
    document.body.appendChild(m);
    S.menu = m;
    return m;
  }

  document.addEventListener("contextmenu", (e) => {
    const img = e.target && e.target.closest && e.target.closest("img");
    const p = img && pathOf(img);
    if (!p) return;
    e.preventDefault();
    menu(p, e.clientX, e.clientY);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  if (window.MutationObserver) {
    new MutationObserver(soon).observe(document.documentElement,
      { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
  }

  window.ekiSensitive = { load, is, mark, blurOn, setBlur, menu, pathOf, apply };
  load();

  // ---- Settings → Pictures: this window's preference, not routing.json -------------------------

  function form() {
    const f = window.settingsForm;
    const box = `<label class="set-check"><input type="checkbox" data-f="blur"${blurOn() ? " checked" : ""}> Blur sensitive pictures</label>`;
    const sub = "Pictures are marked automatically when a local classifier is installed; otherwise mark them " +
      "yourself: right-click a picture → Mark sensitive. A sensitive picture is never synced or exported. " +
      "This preference is kept in this window only.";
    return f ? f.group("Pictures", f.line("Sensitive pictures", box, sub)) : box;
  }

  function edit(el) {
    if (el.dataset.f === "blur") setBlur(!!el.checked);
    return false;
  }

  const pictures = { title: "Pictures", file: "routing", html: form, edit };
  // The sidebar link #settings is window.settings until settings.js loads.
  if (window.settings && typeof window.settings.section === "function") settings.section("pictures", pictures);
  else (window.settingsSections = window.settingsSections || []).push(["pictures", pictures]);
})();
