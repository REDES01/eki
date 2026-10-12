"""Sensitive pictures in the window (sensitive.js): blurred wherever an /api/file img shows one."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# A DOM just big enough for sensitive.js: elements with classes, parents and children, a document
# that finds every img, listeners kept by name, a MutationObserver we fire by hand, fetch and storage.
STUB = r"""
const listeners = { window: {}, document: {} };
const on = (where) => (ev, fn) => { (listeners[where][ev] = listeners[where][ev] || []).push(fn); };
function el(tag, attrs) {
  const e = { tagName: tag.toUpperCase(), attrs: attrs || {}, children: [], parentElement: null, style: {}, dataset: {},
    _cls: new Set(), innerHTML: "",
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    appendChild(c) { if (c.parentElement) c.parentElement.children.splice(c.parentElement.children.indexOf(c), 1);
      c.parentElement = this; this.children.push(c); return c; },
    insertBefore(c, ref) { c.parentElement = this; this.children.splice(this.children.indexOf(ref), 0, c); return c; },
    remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); },
    contains(x) { for (; x; x = x.parentElement) if (x === this) return true; return false; },
    closest(sel) { for (let x = this; x; x = x.parentElement) if (matches(x, sel)) return x; return null; },
    querySelectorAll(sel) { const out = []; const walk = (n) => n.children.forEach((c) => { if (matches(c, sel)) out.push(c); walk(c); });
      walk(this); return out; },
    addEventListener(ev, fn) { this["on" + ev] = fn; } };
  e.classList = { add: (c) => e._cls.add(c), remove: (c) => e._cls.delete(c), contains: (c) => e._cls.has(c),
    toggle: (c, f) => { (f === undefined ? !e._cls.has(c) : f) ? e._cls.add(c) : e._cls.delete(c); } };
  Object.defineProperty(e, "className", { set(v) { e._cls = new Set(v.split(/\s+/).filter(Boolean)); }, get() { return [...e._cls].join(" "); } });
  return e;
}
function matches(x, sel) {
  const [tag, ...cls] = sel.split(".");
  return (!tag || x.tagName === tag.toUpperCase()) && cls.every((c) => x._cls.has(c));
}
const body = el("body");
let observed = null;
global.window = global;
window.addEventListener = on("window");
global.document = { documentElement: body, body, createElement: (t) => el(t), addEventListener: on("document"),
  querySelectorAll: (s) => body.querySelectorAll(s) };
global.MutationObserver = class { constructor(f) { observed = f; } observe() {} };
global.getComputedStyle = () => ({ position: "static" });
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
const [sensitive, steps, pref] = JSON.parse(process.argv[3]);
if (pref !== null) store["eki.blurSensitive"] = pref;
const out = { posts: [], seen: [] };
let marked = new Set(sensitive);
global.fetch = async (url, opts) => {
  if (opts && opts.method === "POST") {
    const b = JSON.parse(opts.body); out.posts.push({ url, body: b, xeki: opts.headers["X-Eki"] });
    b.sensitive ? marked.add(b.path) : marked.delete(b.path);
    return { ok: true, json: async () => b };
  }
  return { ok: true, json: async () => ({ paths: [...marked] }) };
};
global.alert = () => {};
const tick = () => new Promise((r) => setTimeout(r, 0));
const pic = (p, extra) => { const b = el("button"); b.className = "gpic";
  b.appendChild(el("img", { src: "/api/file?path=" + encodeURIComponent(p) + (extra || "") })); body.appendChild(b); return b; };
const ev = (target) => { const e = { target, clientX: 5, clientY: 6, stopped: false, prevented: false,
  preventDefault() { e.prevented = true; }, stopPropagation() { e.stopped = true; }, stopImmediatePropagation() { e.stopped = true; } }; return e; };
require(process.argv[2]);
const S = window.ekiSensitive;
const state = () => body.querySelectorAll("img").map((i) => [i.attrs.src, i.classList.contains("sens-blur"),
  !!(i.parentElement && i.parentElement.classList.contains("sens-on"))]);
(async () => {
  await tick();
  out.blurOn = S.blurOn();
  for (const s of steps) {
    if (s[0] === "pic") { pic(s[1], s[2]); observed([]); await tick(); }
    if (s[0] === "blur") { S.setBlur(s[1]); }
    if (s[0] === "click") { const img = body.querySelectorAll("img")[s[1]]; const e = ev(img);
      listeners.window.click.forEach((f) => f(e)); out.click = { stopped: e.stopped, prevented: e.prevented }; await tick(); }
    if (s[0] === "menu") { const img = body.querySelectorAll("img")[s[1]]; const e = ev(img);
      listeners.document.contextmenu.forEach((f) => f(e));
      const m = body.querySelectorAll("div.sens-menu")[0]; out.menu = m ? m.innerHTML : null; out.menuPrevented = e.prevented;
      if (m && s[2]) { await m.onclick({ stopPropagation() {} }); await tick(); } }
    out.seen.push(state());
  }
  out.store = store;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


def run(tmp_path, sensitive, steps, pref=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "sensitive.js"), json.dumps([sensitive, steps, pref])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_sensitive_picture_is_blurred_and_a_plain_one_not(tmp_path):
    got = run(tmp_path, ["/p/a.png"], [["pic", "/p/a.png"], ["pic", "/p/b.png"], ["pic", "/p/a.png", "&t=5"],
                                       ["pic", "/p//./a.png"]])
    (a, a_on, a_label), (b, b_on, b_label), (a2, a2_on, _), (a3, a3_on, _) = got["seen"][-1]
    assert a_on and a_label and not b_on and not b_label and a2_on and a3_on


def test_blur_off_takes_it_away_and_on_brings_it_back(tmp_path):
    got = run(tmp_path, ["/p/a.png"], [["pic", "/p/a.png"], ["blur", False], ["blur", True]])
    assert [s[0][1] for s in got["seen"]] == [True, False, True]
    assert got["store"]["eki.blurSensitive"] == "1"


def test_the_preference_is_on_by_default_and_0_is_off(tmp_path):
    assert run(tmp_path, [], [])["blurOn"] is True
    off = run(tmp_path, ["/p/a.png"], [["pic", "/p/a.png"]], pref="0")
    assert off["blurOn"] is False and off["seen"][0][0][1] is False


def test_a_click_reveals_it_and_goes_no_further(tmp_path):
    got = run(tmp_path, ["/p/a.png"], [["pic", "/p/a.png"], ["click", 0], ["pic", "/p/a.png"]])
    assert got["click"] == {"stopped": True, "prevented": True}
    assert got["seen"][1][0][1:] == [False, False]
    assert got["seen"][2][1][1] is False          # the same picture drawn again stays shown this page view


def test_a_click_on_a_plain_picture_is_left_alone(tmp_path):
    got = run(tmp_path, [], [["pic", "/p/b.png"], ["click", 0]])
    assert got["click"] == {"stopped": False, "prevented": False}


def test_right_click_marks_it_with_a_post(tmp_path):
    got = run(tmp_path, [], [["pic", "/p/b.png"], ["menu", 0, True]])
    assert got["menuPrevented"] and "Mark sensitive" in got["menu"]
    assert got["posts"] == [{"url": "/api/sensitive", "body": {"path": "/p/b.png", "sensitive": True}, "xeki": "1"}]
    assert got["seen"][-1][0][1] is True


def test_right_click_on_a_sensitive_one_offers_not_sensitive(tmp_path):
    got = run(tmp_path, ["/p/a.png"], [["pic", "/p/a.png"], ["menu", 0, True]])
    assert "Not sensitive" in got["menu"]
    assert got["posts"][0]["body"] == {"path": "/p/a.png", "sensitive": False}
    assert got["seen"][-1][0][1] is False


def test_the_page_loads_it_after_md_and_settings_lists_pictures():
    html = (WEB / "index.html").read_text()
    assert '<script src="/ui/md.js"></script>\n<script src="/ui/sensitive.js"></script>' in html
    js = (WEB / "sensitive.js").read_text()
    assert 'settings.section("pictures"' in js and 'push(["pictures"' in js and "Blur sensitive pictures" in js
    assert "right-click" in js and "eki.blurSensitive" in js
    assert '"pictures"' in (WEB / "settings.js").read_text().split("const SECTIONS = ")[1].split("\n")[0]
    for cls in ("img.sens-blur", ".sens-on::after", ".sens-menu"):
        assert cls in (WEB / "style.css").read_text(), cls
    assert len(js.splitlines()) <= 400
