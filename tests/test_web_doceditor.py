"""The doc editor in the side panel (doceditor.js): saves with the hash it loaded, conflicts keep your text."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough DOM to draw the editor, press keys, click its buttons and read what it shows.
STUB = """
const all = [];
function mk(tag) {
  const e = { tag, className: "", id: "", hidden: false, value: "", children: [], listeners: {}, type: "",
    scrollTop: 0, scrollHeight: 1000, clientHeight: 200, selectionStart: 0, selectionEnd: 0,
    _text: "",
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(t) { this._text = String(t); this.children = []; },
    get innerHTML() { return this.textContent; },
    set innerHTML(h) { this._text = String(h); this.children = []; },
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    appendChild(c) { this.children.push(c); c.parent = this; return c; } };
  all.push(e);
  return e;
}
const visible = (e) => !e.hidden && (!e.parent || visible(e.parent));
global.window = {};
global.document = { createElement: mk, getElementById: (id) => all.find((e) => e.id === id) || null,
  head: mk("head") };
global.setTimeout = () => 1; global.clearTimeout = () => {};
const [doc, replies, steps] = JSON.parse(process.argv[3]);
const out = { posts: [], done: null, log: [] };
global.fetch = async (url, opts) => {
  out.posts.push({ url, method: opts.method, headers: opts.headers, body: JSON.parse(opts.body) });
  const r = replies.shift();
  if (r === "down") throw new Error("no route");
  return { ok: !r.error, status: r.error ? 400 : 200, json: async () => r };
};
require(process.argv[2]);
const body = mk("div"); body.scrollTop = 400; body.scrollHeight = 1000; body.clientHeight = 200;
const area = window.doceditor.open(body, doc, (d) => { out.done = d; });
const buttons = () => all.filter((e) => e.tag === "button" && visible(e));
const click = (t) => { const b = buttons().find((b) => b.textContent === t); if (!b) throw new Error("no button " + t); return b.listeners.click(); };
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  out.ratio = area.scrollTop / (area.scrollHeight - area.clientHeight);
  for (const s of steps) {
    if (s[0] === "type") area.value = s[1];
    if (s[0] === "select") { area.selectionStart = s[1]; area.selectionEnd = s[2]; }
    if (s[0] === "key") { const e = Object.assign({ prevented: false, preventDefault() { this.prevented = true; } }, s[1]);
                          area.listeners.keydown(e); out.log.push(["prevented", e.prevented]); }
    if (s[0] === "blur") area.listeners.blur();
    if (s[0] === "click") await click(s[1]);
    if (s[0] === "flush") { await window.doceditor.flush(); out.log.push(["flushed"]); }
    if (s[0] === "dirty") out.log.push(["dirty", window.doceditor.dirty()]);
    if (s[0] === "snap") out.log.push(["snap", {
      value: area.value, sel: [area.selectionStart, area.selectionEnd],
      shown: all.filter((e) => visible(e) && e.parent && e.tag !== "button" && e.children.length === 0 && e._text)
                .map((e) => e._text),
      buttons: buttons().map((b) => b.textContent) }]);
    await tick(); await tick();
  }
  out.css = !!document.getElementById("doceditor-css");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

DOC = {"path": "/Users/me/notes.md", "name": "notes.md", "text": "# Notes\n", "mtime": 100.0, "hash": "h0",
       "size": 8, "editable": True, "reason": "", "writer": None}
SAVE = {"key": "s", "metaKey": True}


def run(tmp_path, steps, replies=(), doc=DOC):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "doceditor.js"), json.dumps([doc, list(replies), list(steps)])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def snaps(got):
    return [x[1] for x in got["log"] if x[0] == "snap"]


def test_cmd_s_posts_with_the_loaded_hash_then_a_saved_toast(tmp_path):
    got = run(tmp_path, [["type", "# Notes\nmore\n"], ["key", SAVE], ["snap"]],
              [{"saved": True, "mtime": 101.0, "hash": "h1"}])
    assert got["css"] and abs(got["ratio"] - 0.5) < 1e-9
    assert ["prevented", True] in got["log"]
    (p,) = got["posts"]
    assert p["url"] == "/api/doc" and p["method"] == "POST" and p["headers"]["X-Eki"] == "1"
    assert p["headers"]["Content-Type"] == "application/json"
    assert p["body"] == {"path": DOC["path"], "text": "# Notes\nmore\n", "base_hash": "h0"}
    assert "Saved" in snaps(got)[0]["shown"]


def test_a_second_save_uses_the_new_hash_and_unchanged_text_does_not_post(tmp_path):
    got = run(tmp_path, [["key", SAVE], ["type", "a"], ["key", SAVE], ["key", SAVE], ["type", "b"], ["blur"]],
              [{"saved": True, "mtime": 101.0, "hash": "h1"}, {"saved": True, "mtime": 102.0, "hash": "h2"}])
    assert [p["body"]["base_hash"] for p in got["posts"]] == ["h0", "h1"]
    assert [p["body"]["text"] for p in got["posts"]] == ["a", "b"]


def test_tab_inserts_two_spaces_and_shift_tab_outdents_selected_lines(tmp_path):
    got = run(tmp_path, [["type", "ab"], ["select", 1, 1], ["key", {"key": "Tab"}], ["snap"],
                         ["type", "x\n  y\n z\n"], ["select", 0, 9], ["key", {"key": "Tab"}], ["snap"],
                         ["key", {"key": "Tab", "shiftKey": True}], ["key", {"key": "Tab", "shiftKey": True}], ["snap"]])
    s = snaps(got)
    assert s[0]["value"] == "a  b" and s[0]["sel"] == [3, 3]
    assert s[1]["value"] == "  x\n    y\n   z\n"
    assert s[2]["value"] == "x\ny\nz\n"
    assert got["posts"] == []


def test_a_conflict_keeps_the_typed_text_and_offers_three_ways(tmp_path):
    disk = {"conflict": True, "text": "agent wrote this\n", "mtime": 150.0, "hash": "hd"}
    got = run(tmp_path, [["type", "mine\n"], ["key", SAVE], ["snap"], ["dirty"], ["click", "Keep mine"], ["dirty"]],
              [disk, {"saved": True, "mtime": 151.0, "hash": "h9"}])
    s = snaps(got)[0]
    assert s["value"] == "mine\n" and "changed on disk" in s["shown"]
    assert {"Reload", "Keep mine", "Show both"} <= set(s["buttons"])
    assert got["posts"][1]["body"] == {"path": DOC["path"], "text": "mine\n", "base_hash": "hd"}
    assert ["dirty", True] in got["log"] and got["log"][-1] == ["dirty", False]


def test_reload_puts_the_disk_text_in_and_saves_from_its_hash(tmp_path):
    disk = {"conflict": True, "text": "agent wrote this\n", "mtime": 150.0, "hash": "hd"}
    got = run(tmp_path, [["type", "mine\n"], ["key", SAVE], ["click", "Reload"], ["snap"], ["dirty"],
                         ["type", "agent wrote this\nand me\n"], ["key", SAVE]],
              [disk, {"saved": True, "mtime": 160.0, "hash": "h2"}])
    s = snaps(got)[0]
    assert s["value"] == "agent wrote this\n" and "changed on disk" not in s["shown"]
    assert ["dirty", False] in got["log"]
    assert got["posts"][1]["body"]["base_hash"] == "hd"


def test_show_both_draws_mine_and_disk_side_by_side(tmp_path):
    disk = {"conflict": True, "text": "agent wrote this\n", "mtime": 150.0, "hash": "hd"}
    got = run(tmp_path, [["type", "mine\n"], ["key", SAVE], ["click", "Show both"], ["snap"], ["click", "Back"], ["snap"]],
              [disk])
    both, back = snaps(got)
    assert "mine\n" in both["shown"] and "agent wrote this\n" in both["shown"]
    assert {"Reload", "Keep mine", "Back"} <= set(both["buttons"])
    assert "agent wrote this\n" not in back["shown"] and "Show both" in back["buttons"]


def test_a_failed_save_shows_the_error_and_keeps_the_text(tmp_path):
    got = run(tmp_path, [["type", "mine"], ["key", SAVE], ["snap"], ["type", "mine2"], ["key", SAVE], ["snap"], ["dirty"]],
              ["down", {"error": "too large"}])
    a, b = snaps(got)
    assert a["value"] == "mine" and any("save failed" in t for t in a["shown"])
    assert b["value"] == "mine2" and "too large" in b["shown"]
    assert got["log"][-1] == ["dirty", True]


def test_flush_saves_pending_text(tmp_path):
    got = run(tmp_path, [["type", "pending"], ["dirty"], ["flush"], ["dirty"]],
              [{"saved": True, "mtime": 101.0, "hash": "h1"}])
    assert got["posts"][0]["body"]["text"] == "pending"
    assert got["log"] == [["dirty", True], ["flushed"], ["dirty", False]]


def test_done_saves_then_hands_back_the_latest_doc(tmp_path):
    got = run(tmp_path, [["type", "final"], ["click", "Done"]], [{"saved": True, "mtime": 120.0, "hash": "h7"}])
    assert got["done"]["text"] == "final" and got["done"]["hash"] == "h7" and got["done"]["mtime"] == 120.0
    assert got["done"]["path"] == DOC["path"]


def test_done_stays_while_a_conflict_shows(tmp_path):
    disk = {"conflict": True, "text": "x", "mtime": 1.0, "hash": "hd"}
    got = run(tmp_path, [["type", "final"], ["click", "Done"]], [disk])
    assert got["done"] is None


def test_the_editor_stays_small():
    assert len((WEB / "doceditor.js").read_text().splitlines()) <= 400
