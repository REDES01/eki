"""The Artifacts page: what your chats made, under day headers, from /api/artifacts (artifacts.js)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# artifacts.js needs a page; this stub is just enough to load it, click it and see what it fetched and showed.
STUB = """
const els = {};
const mk = (id) => ({ id, innerHTML: "", textContent: "", value: "", hidden: false, dataset: {}, listeners: {},
  classList: { add() {}, remove() {}, toggle() {} }, children: [],
  addEventListener(ev, fn) { this.listeners[ev] = fn; }, appendChild(c) { this.children.push(c); },
  insertBefore(c) { els[c.id] = c; } });
const el = (id) => els[id] || (els[id] = mk(id));
global.window = { addEventListener() {} };
global.document = { getElementById: (id) => els[id] || (els["artifacts-view"] || ["main", "composer", "title", "meta", "panel-body"].includes(id) ? el(id) : null),
  createElement: () => mk(""), head: { appendChild() {} }, querySelectorAll: () => [] };
global.setTimeout = (f) => 0; global.clearTimeout = () => {};
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
const out = { fetched: [], shown: [], registered: null };
global.nav = { register: (name, v) => { out.registered = name; } };
global.panel = { show: async (p) => { out.shown.push(p); } };
const [items, now, steps] = JSON.parse(process.argv[3]);
global.fetch = async (url) => { out.fetched.push(url); return { ok: true, json: async () => ({ items, more: true }) }; };
require(process.argv[2]);
const A = window.artifacts;
// a click on the page: target.closest(sel) answers for the one selector given
const click = (sel, dataset) => els["artifacts-view"].listeners.click({ target: { closest: (s) => s === sel ? { dataset } : null } });
(async () => {
  out.days = A.days(items, now).map((d) => [d.label, d.items.map((i) => i.name)]);
  for (const s of steps) {
    if (s[0] === "show") await A.show(s[1]);
    if (s[0] === "kind") await click("[data-kind]", { kind: s[1] });
    if (s[0] === "more") await click("#af-more", {});
    if (s[0] === "open") { click("[data-art]", { art: String(s[1]) }); await new Promise((r) => setImmediate(r)); }
  }
  out.list = els["af-list"] ? els["af-list"].innerHTML : "";
  out.detail = els["panel-body"] ? els["panel-body"].children.map((c) => c.innerHTML).join("") : "";
  process.stdout.write(JSON.stringify(out));
})();
"""

NOW = 1791720000.0                        # 2026-10-11, around noon in most zones


def run(tmp_path, items, steps=()):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "artifacts.js"), json.dumps([items, NOW, list(steps)])],
                         capture_output=True, text=True, timeout=20, env={"TZ": "UTC", "PATH": shutil.os.environ["PATH"]})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def item(name, ago, kind="page", **kw):
    it = {"path": f"/tmp/x/{name}", "name": name, "kind": kind, "ext": Path(name).suffix, "run": "r1",
          "thread": "t1", "thread_title": "the chat", "project": None, "created_at": NOW - ago,
          "prompt": "make it", "provider": "claude"}
    it.update(kw)
    return it


def test_items_group_into_today_yesterday_then_dates_in_order(tmp_path):
    items = [item("a.html", 3600), item("b.md", 2 * 3600), item("c.pdf", 86400), item("d.svg", 5 * 86400, "picture")]
    days = run(tmp_path, items)["days"]
    assert [d[0] for d in days[:2]] == ["Today", "Yesterday"]
    assert days[0][1] == ["a.html", "b.md"] and days[1][1] == ["c.pdf"] and days[2][1] == ["d.svg"]
    assert len(days) == 3 and "Oct" in days[2][0]


def test_pictures_preselects_and_the_seg_sends_the_kind(tmp_path):
    got = run(tmp_path, [item("a.png", 60, "picture")],
              [["show", "picture"], ["kind", "page"], ["kind", "document"], ["kind", "all"]])
    assert got["registered"] == "artifacts"
    kinds = [u.split("kind=")[1].split("&")[0] for u in got["fetched"]]
    assert kinds == ["picture", "page", "document", "all"]
    assert all(u.startswith("/api/artifacts?") and "before=" not in u for u in got["fetched"])


def test_more_pages_from_the_last_items_time(tmp_path):
    items = [item("a.html", 60), item("b.md", 600)]
    got = run(tmp_path, items, [["show", None], ["more"]])
    assert len(got["fetched"]) == 2 and f"before={int(NOW - 600)}" in got["fetched"][1]


def test_pictures_are_thumbnails_and_the_rest_small_cards(tmp_path):
    got = run(tmp_path, [item("a.png", 60, "picture"), item("b.html", 120)], [["show", None]])
    html = got["list"]
    assert 'class="gpic"' in html and "<img" in html and "/api/file?path=%2Ftmp%2Fx%2Fa.png" in html
    assert 'class="card af-doc"' in html and "🌐" in html and ">b.html<" in html and "the chat" in html


def test_a_click_shows_the_file_and_the_way_to_its_chat(tmp_path):
    got = run(tmp_path, [item("a.html", 60, thread="t42")], [["show", None], ["open", 0]])
    assert got["shown"] == ["/tmp/x/a.html"]
    assert 'href="#t42"' in got["detail"] and "Go to chat" in got["detail"] and "make it" in got["detail"]


def test_the_page_stays_small():
    assert len((WEB / "artifacts.js").read_text().splitlines()) <= 400
