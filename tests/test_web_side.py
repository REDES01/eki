"""The sidebar groups chats in a project under its header; the rest by date, as before."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# side.js and its listeners need a page; this stub is just enough to load it and call render().
STUB = """
const el = () => ({ innerHTML: "", textContent: "", value: "", appendChild() {}, remove() {} });
global.window = { addEventListener() {} };
global.document = { getElementById: el, addEventListener() {}, createElement: el };
global.fetch = () => new Promise(() => {});
global.setInterval = () => 0;
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
global.nav = { current: () => null };
global.prs = { lines: () => "", total: () => 0 };
require(process.argv[2]);
const [list, cur] = JSON.parse(process.argv[3]);
process.stdout.write(window.side.render(list, cur));
"""


def render(tmp_path, threads, cur=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "side.js"), json.dumps([threads, cur])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout


def thread(tid, ago, project=None, title=None):
    return {"id": tid, "title": title or tid, "updated": time.time() - ago, "provider": "claude",
            "working": False, "needs_you": False, "project": project}


def test_project_chats_sit_under_their_header_and_the_rest_by_date(tmp_path):
    tok = {"id": "p1", "name": "tokenbar"}
    eki = {"id": "eki", "name": "eki"}
    html = render(tmp_path, [thread("a", 10), thread("b", 20, tok, "newer"), thread("c", 30, eki),
                             thread("d", 40, tok, "older"), thread("e", 9 * 86400)])
    assert 'href="#project/p1"' in html and 'href="#project/eki"' in html
    assert ">tokenbar<" in html and "2 chats" in html and "1 chat<" in html
    assert html.index("#project/p1") < html.index("#project/eki")    # the project with the latest chat first
    assert html.index("#project/eki") < html.index("Today") < html.index('href="#a"') < html.index("Older")
    assert 'href="#b"' not in html and 'href="#e"' in html               # closed until opened


def test_the_current_threads_project_is_open_newest_first(tmp_path):
    tok = {"id": "p1", "name": "tokenbar"}
    html = render(tmp_path, [thread("d", 40, tok), thread("b", 20, tok)], cur="d")
    assert 'class="pgroup open"' in html
    assert html.index('href="#b"') < html.index('href="#d"') and 'class="thread on" data-id="d"' in html
    assert "tgroup" not in html                                           # no loose chats, no date headers


def test_without_projects_it_is_as_before(tmp_path):
    html = render(tmp_path, [thread("a", 10), thread("b", 2 * 86400)])
    assert "pgroup" not in html and html.index("Today") < html.index('href="#a"')
    assert "No threads yet." in render(tmp_path, [])


def test_the_composer_names_the_project_and_sends_no_folder():
    app = (WEB / "app.js").read_text()
    assert 'state.project = t.project || null' in app
    assert '"📁 " + (state.project.name' in app
    assert 'cwd: state.project ? "" :' in app
    assert '.closest(".mrow").hidden = !!state.project' in app


def test_the_window_button_room_scales_back_with_the_shell_zoom():
    css = (WEB / "style.css").read_text()
    rule = lambda sel: next(l for l in css.splitlines() if l.startswith(sel + " {"))
    mac = rule('[data-shell="mac"] .side-head')
    assert "78px" in mac and "var(--eki-zoom, 1)" in mac
    full = rule('[data-shell="mac"][data-fullscreen="1"] .side-head')
    assert "14px" in full and "--eki-zoom" not in full
