"""The sidebar: eki's threads that need you, Pinned, your groups, then chats in a project under its header and the
rest by date, as before; an Archived line at the bottom."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# sidelist.js and side.js and their listeners need a page; this stub is just enough to load them and call render().
STUB = """
const el = () => ({ innerHTML: "", textContent: "", value: "", appendChild() {}, remove() {} });
global.window = { addEventListener() {} };
global.document = { getElementById: el, addEventListener() {}, createElement: el, visibilityState: "visible" };
global.fetch = () => new Promise(() => {});
global.setInterval = () => 0;
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
global.nav = { current: () => null };
global.prs = { lines: () => "", total: () => 0 };
require(process.argv[2]);
global.sidelist = window.sidelist;
require(process.argv[3]);
const [list, cur, groups, archived, open] = JSON.parse(process.argv[4]);
if (open) window.sidelist.state.archived = true;
process.stdout.write(archived ? window.side.render(list, cur, groups, archived) : window.side.render(list, cur, groups));
"""


def render(tmp_path, threads, cur=None, groups=None, archived=None, open_archived=False):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    args = json.dumps([threads, cur, groups or [], archived, open_archived])
    out = subprocess.run(["node", str(stub), str(WEB / "sidelist.js"), str(WEB / "side.js"), args],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout


def thread(tid, ago, project=None, title=None, **kw):
    t = {"id": tid, "title": title or tid, "updated": time.time() - ago, "provider": "claude",
         "working": False, "needs_you": False, "project": project, "pinned": False, "unread": False,
         "own": False, "title_by": "prompt", "group": None, "archived": False, "sessions": []}
    t.update(kw)
    return t


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


def test_eki_needing_you_then_pinned_then_groups_then_projects_then_dates(tmp_path):
    tok = {"id": "p1", "name": "tokenbar"}
    work = {"id": "g1", "name": "Writing"}
    html = render(tmp_path, [thread("loose", 10), thread("pj", 20, tok), thread("grp", 30, group=work),
                             thread("pin", 40, pinned=True), thread("ekis", 50, own=True, needs_you=True)],
                  groups=[{"id": "g0", "name": "Empty"}, work])
    order = [html.index('href="#ekis"'), html.index("Pinned"), html.index('href="#pin"'), html.index(">Writing<"),
             html.index('href="#grp"'), html.index("#project/p1"), html.index("Today"), html.index('href="#loose"')]
    assert order == sorted(order)
    assert ">Empty<" not in html                                          # an empty group isn't shown
    assert html.index('href="#ekis"') < html.index("tgroup")             # no header over eki's


def test_a_group_of_yours_is_open_until_you_close_it(tmp_path):
    html = render(tmp_path, [thread("grp", 30, group={"id": "g1", "name": "Writing"})])
    assert 'data-cgroup="g1"' in html and 'href="#grp"' in html          # open by default


def test_an_unread_chat_has_its_own_dot(tmp_path):
    html = render(tmp_path, [thread("a", 10, unread=True), thread("b", 20)])
    assert html.count('class="dot unread"') == 1
    assert html.index('class="dot unread"') < html.index('href="#b"')
    busy = render(tmp_path, [thread("a", 10, unread=True, working=True)])
    assert "dot unread" not in busy and 'class="dot"' in busy            # working wins over unread


def test_each_chat_has_a_more_button(tmp_path):
    html = render(tmp_path, [thread("a", 10)])
    assert 'data-id="a"' in html and 'class="tmore"' in html and 'data-tmore="a"' in html


def test_a_pinned_project_chat_leaves_its_project(tmp_path):
    tok = {"id": "p1", "name": "tokenbar"}
    html = render(tmp_path, [thread("pin", 10, tok, pinned=True), thread("other", 20, tok)], cur="other")
    assert html.count('href="#pin"') == 1 and html.index('href="#pin"') < html.index("#project/p1")
    assert "1 chat<" in html


def test_the_archived_line_and_its_list(tmp_path):
    arch = [thread("old", 99, archived=True, title="gone")]
    closed = render(tmp_path, [thread("a", 10)], archived=arch)
    assert "Archived (1)" in closed and "data-unarchive" not in closed
    assert "Archived" not in render(tmp_path, [thread("a", 10)])         # nothing archived, no line
    shown = render(tmp_path, [thread("a", 10)], archived=arch, open_archived=True)
    assert 'data-unarchive="old"' in shown and ">gone<" in shown
    assert shown.index('href="#a"') < shown.index("Archived (1)")


def test_the_page_loads_the_list_and_menu_before_the_sidebar():
    html = (WEB / "index.html").read_text()
    assert html.index("/ui/sidelist.js") < html.index("/ui/side.js")
    assert html.index("/ui/threadmenu.js") < html.index("/ui/side.js")


def test_the_chat_marks_itself_seen_only_when_you_can_see_it():
    app = (WEB / "app.js").read_text()
    assert "{ unread: false }" in app
    assert 'document.visibilityState === "visible"' in app
    side = (WEB / "side.js").read_text()
    assert "/api/threads?archived=1" in side and "/api/groups" in side and "{ archive: false }" in side


def test_the_web_files_stay_small():
    for f in ("sidelist.js", "side.js", "app.js"):
        assert len((WEB / f).read_text().splitlines()) <= 400, f
