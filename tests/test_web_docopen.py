"""Open file… (⌘O): path, fuzzy search, recents and `eki open` polling (docopen.js)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page to load docopen.js, press keys in its dialog and see what it fetched and opened.
STUB = """
const els = {};
function mk(tag) {
  const e = { tagName: tag, id: "", innerHTML: "", textContent: "", value: "", hidden: false, dataset: {}, listeners: {},
    children: [], className: "", parentNode: null,
    setAttribute() {}, focus() {},
    get nextSibling() { const k = this.parentNode ? this.parentNode.children : []; return k[k.indexOf(this) + 1] || null; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    appendChild(c) { this.children.push(c); c.parentNode = this; if (c.id) els[c.id] = c; return c; },
    insertBefore(c, ref) { const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, c);
      c.parentNode = this; if (c.id) els[c.id] = c; return c; } };
  return e;
}
const fire = (e, ev, data) => (e.listeners[ev] || []).forEach((f) => f(data));
const side = mk("nav");
const neu = mk("a"); neu.id = "new"; side.appendChild(neu);
const after = mk("a"); after.id = "chat"; side.appendChild(after);
const docListeners = {}, winListeners = {};
const opts = JSON.parse(process.argv[3]);
const out = { fetched: [], shown: [], focused: 0 };
global.window = { addEventListener(ev, fn) { (winListeners[ev] = winListeners[ev] || []).push(fn); },
  dispatchEvent(e) { (winListeners[e.type] || []).forEach((f) => f(e)); }, focus() { out.focused++; } };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
global.location = { hash: opts.hash || "" };
global.document = { getElementById: (id) => els[id] || null, createElement: (t) => mk(t),
  head: mk("head"), body: mk("body"),
  addEventListener(ev, fn) { (docListeners[ev] = docListeners[ev] || []).push(fn); } };
const store = {};
global.localStorage = opts.badStorage
  ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } }
  : { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
let timers = [];
global.setTimeout = (f) => { timers.push(f); return timers.length; };
global.clearTimeout = () => {};
let interval = null;
global.setInterval = (f) => { interval = f; return 1; };
global.panel = { show: async (p) => { out.shown.push(p); } };
const opens = opts.opens || [];
global.fetch = async (url) => {
  out.fetched.push(url);
  let body = {};
  if (url.startsWith("/api/doc/search")) body = { files: opts.files || [] };
  else if (url.startsWith("/api/doc/opens")) body = url.includes("after=") ? { opens, last: 9 } : { opens: [{ id: 3, path: "/old.md" }], last: 3 };
  else if (url.startsWith("/api/threads/")) body = { project: opts.threadProject || null };
  return { ok: true, statusText: "OK", json: async () => body };
};
const tick = () => new Promise((r) => setImmediate(r));
const key = (k, more) => Object.assign({ key: k, preventDefault() { out.prevented = (out.prevented || 0) + 1; }, stopPropagation() {} }, more || {});
require(process.argv[2]);
(async () => {
  await tick();
  out.sidebar = side.children.map((c) => [c.id, c.textContent, c.title || ""]);
  const back = () => document.body.children[0];
  const input = () => back().children[0].children[0];
  const list = () => back().children[0].children[2];
  for (const s of opts.steps || []) {
    if (s[0] === "cmdo") (docListeners.keydown || []).forEach((f) => f(key("o", { metaKey: true })));
    if (s[0] === "type") { input().value = s[1]; fire(input(), "input", {}); const t = timers; timers = []; t.forEach((f) => f()); }
    if (s[0] === "key") fire(input(), "keydown", key(s[1]));
    if (s[0] === "doc") window.dispatchEvent(new CustomEvent("eki:doc", { detail: { path: s[1] } }));
    if (s[0] === "poll") await interval();
    if (s[0] === "show") window.docopen.show();
    for (let i = 0; i < 5; i++) await tick();
  }
  out.open = back() ? !back().hidden : false;
  out.list = back() ? list().innerHTML : "";
  out.recent = window.docopen.recent();
  out.css = document.head.children.map((c) => c.id);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run(tmp_path, **opts):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "docopen.js"), json.dumps(opts)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


FILES = [{"path": "/Users/me/game/notes/playtest.md", "rel": "notes/playtest.md", "root": "/Users/me/game", "mtime": 1},
         {"path": "/Users/me/game/play.md", "rel": "play.md", "root": "/Users/me/game", "mtime": 2}]


def test_sidebar_gets_open_file_right_after_new(tmp_path):
    got = run(tmp_path)
    assert [c[0] for c in got["sidebar"]] == ["new", "docopen-link", "chat"]
    assert got["sidebar"][1][1] == "Open file…" and got["sidebar"][1][2] == "Open file (⌘O)"


def test_cmd_o_opens_the_dialog(tmp_path):
    got = run(tmp_path, steps=[["cmdo"]])
    assert got["open"] and got["prevented"] >= 1 and "docopen-css" in got["css"]


def test_typing_searches_lists_and_enter_opens_the_choice(tmp_path):
    got = run(tmp_path, files=FILES, steps=[["cmdo"], ["type", "play"]])
    assert any(u.startswith("/api/doc/search?q=play") and "project=" not in u for u in got["fetched"])
    assert "notes/playtest.md" in got["list"] and "play.md" in got["list"]
    assert "game" in got["list"]                        # no project: each result names its root's folder
    got = run(tmp_path, files=FILES, steps=[["cmdo"], ["type", "play"], ["key", "ArrowDown"], ["key", "Enter"]])
    assert got["shown"] == ["/Users/me/game/play.md"] and not got["open"]


def test_search_on_a_project_page_sends_the_project(tmp_path):
    got = run(tmp_path, hash="#project/p1", files=FILES, steps=[["cmdo"], ["type", "play"]])
    assert any(u == "/api/doc/search?q=play&project=p1" for u in got["fetched"])


def test_search_in_a_projects_chat_sends_its_project(tmp_path):
    got = run(tmp_path, hash="#abc123", threadProject={"id": "p2", "name": "Game"}, files=FILES,
              steps=[["cmdo"], ["type", "play"]])
    assert "/api/doc/search?q=play&project=p2" in got["fetched"]


def test_a_path_opens_directly(tmp_path):
    got = run(tmp_path, steps=[["cmdo"], ["type", "~/notes/a.md"], ["key", "Enter"]])
    assert got["shown"] == ["~/notes/a.md"]
    assert not any(u.startswith("/api/doc/search") for u in got["fetched"])


def test_opened_docs_land_in_recents_which_an_empty_input_lists(tmp_path):
    got = run(tmp_path, steps=[["doc", "/Users/me/a.md"], ["doc", "/Users/me/b.md"], ["doc", "/Users/me/a.md"], ["cmdo"]])
    assert got["recent"] == ["/Users/me/a.md", "/Users/me/b.md"]
    assert "a.md" in got["list"] and "b.md" in got["list"]
    got = run(tmp_path, steps=[["doc", "/Users/me/a.md"], ["cmdo"], ["key", "Enter"]])
    assert got["shown"] == ["/Users/me/a.md"]


def test_recents_keep_twelve(tmp_path):
    got = run(tmp_path, steps=[["doc", f"/x/{i}.md"] for i in range(15)])
    assert len(got["recent"]) == 12 and got["recent"][0] == "/x/14.md"


def test_blocked_storage_breaks_nothing(tmp_path):
    got = run(tmp_path, badStorage=True, files=FILES,
              steps=[["doc", "/Users/me/a.md"], ["cmdo"], ["type", "play"], ["key", "Enter"]])
    assert got["recent"] == [] and got["shown"] == ["/Users/me/game/notes/playtest.md"]


def test_eki_open_poll_shows_the_newest_open(tmp_path):
    got = run(tmp_path, opens=[{"id": 4, "path": "/Users/me/a.md", "t": 1}, {"id": 5, "path": "/Users/me/b.md", "t": 2}],
              steps=[["poll"]])
    assert got["fetched"][0] == "/api/doc/opens"                 # the first look only learns where the queue is
    assert "/api/doc/opens?after=3" in got["fetched"]
    assert got["shown"] == ["/Users/me/b.md"] and got["focused"] == 1
