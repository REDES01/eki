"""The Preview links card in the window: cardserve.js draws a row per running copy (card.live.links) with its
task, address, an Open link that leaves the window (target=_blank), when it started and when it stops if
idle, then "your copy: :4200"; with no links it says so. projectcardpanel.js loads it."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "cardserve.js").read_text()

STUB = r"""
const fs = require("fs");
const [, , part, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}) };
eval(fs.readFileSync(part, "utf8"));
const box = { innerHTML: "" };
const k = window.projectCardKinds.serve;
k.draw(box, JSON.parse(args), "game", "", { save() {}, problems: {}, editable: true });
const first = box.innerHTML;
box.innerHTML = "kept";
k.draw(box, JSON.parse(args), "game", "", { save() {}, problems: {}, editable: true });
const again = box.innerHTML;
k.close();
process.stdout.write(JSON.stringify({ html: first, again, keys: k.keys, title: k.title }));
"""


def draw(tmp_path, live):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    card = {"name": "web", "kind": "serve", "spec": {"kind": "serve"}, "live": live}
    got = subprocess.run(["node", str(stub), str(WEB / "cardserve.js"), json.dumps(card)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_the_panel_loads_it_and_it_parses():
    assert '"cardserve.js"' in (WEB / "projectcardpanel.js").read_text()
    assert 'href="/ui/cardserve.css"' in JS and (WEB / "cardserve.css").is_file()
    assert len(JS.splitlines()) < 400
    if shutil.which("node"):
        out = subprocess.run(["node", "--check", str(WEB / "cardserve.js")], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def test_rows_with_tasks_open_links_times_and_your_copy(tmp_path):
    now = time.time()
    got = draw(tmp_path, {
        "links": [{"folder": "/w/a1", "url": "http://127.0.0.1:47001/chapter1/", "task": "Convert dialogue <v2>",
                   "run": "r1", "started": now - 12 * 60, "stops_at": now + 18 * 60 + 20},
                  {"folder": "/w/river-b2/", "url": "http://127.0.0.1:47002/chapter1/", "task": "", "run": "",
                   "started": None, "stops_at": None}],
        "own": {"folder": "/game", "url": "http://127.0.0.1:4200/chapter1/", "up": True}})
    h = got["html"]
    assert got["title"] == "Preview links" and got["keys"] == []
    assert h.count('class="cs-row"') == 2
    assert "Convert dialogue &lt;v2&gt;" in h and ">river-b2<" in h                 # no task: the folder's name
    assert "127.0.0.1:47001/chapter1/" in h
    assert h.count('target="_blank" rel="noopener"') == 3                          # two Opens and your copy
    assert '<a class="pj-btn cs-open" href="http://127.0.0.1:47002/chapter1/" target="_blank" rel="noopener">Open</a>' in h
    assert "started 12 min ago" in h and "stops in 18 min if idle" in h
    assert 'your copy: <a href="http://127.0.0.1:4200/chapter1/" target="_blank" rel="noopener">:4200</a>' in h
    assert "not running" not in h and "No preview links" not in h
    assert got["again"] == "kept"                              # the 5 s update leaves an unchanged part alone


def test_no_links_and_your_copy_down_or_unknown(tmp_path):
    h = draw(tmp_path, {"links": [], "own": {"folder": "/game", "url": "http://127.0.0.1:4200/", "up": False}})["html"]
    assert "No preview links running — each agent gets one when it starts working." in h
    assert ">:4200</a>" in h and "not running" in h
    h = draw(tmp_path, {"links": [], "own": {"folder": "/game", "url": "", "up": False}})["html"]
    assert "your copy: not running" in h and "href" not in h
    assert "No preview links running" in draw(tmp_path, {})["html"]                # a card without live
