"""The project pages: #projects routes to projectspage.js and #project/<id> to project.js, which reads and acts on
/api/projects."""
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from eki import server

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def test_the_page_links_projects_and_loads_its_script(web):
    page = get(web + "/")[1]
    assert '<a id="projects" href="#projects" class="side-link">Projects</a>' in page
    assert page.index("/ui/nav.js") < page.index("/ui/projectspage.js") < page.index("/ui/project.js") < page.index("/ui/app.js")


def test_project_js_names_the_api_and_the_page_parts(web):
    code, js = get(web + "/ui/project.js")
    assert code == 200
    for name in ('"/api/projects/"', '"/folder"', '"/clone"', '"/api/ask"',
                 "project: S.id", '"X-Eki": "1"', "data-file", "panel.show", 'nav.register("project"',
                 '"/describe"', "eki itself — its own source", "pj-desc-edit", "New chat here", "Clone to ~/",
                 "cloning", "your real folder.", "Background: ", "} open`", "data-sect", "projectspage.js", "pages.css"):
        assert name in js, name
    assert "Have an agent write instructions" not in js   # the Cards area suggests an instructions card
    assert 'nav.register("projects"' not in js and "New project" not in js    # the list is projectspage.js now
    css = get(web + "/ui/style.css")[1]
    assert "#project-view" in css and ".pj-sect.open .pj-fold" in css


def test_the_project_page_has_the_delete_menu(web):
    js = get(web + "/ui/project.js")[1]
    for name in ('id="pj-top"', "window.projectMenu.button(p)", '"[data-pm]"', "e.stopPropagation()",
                 'nav.go("#projects")', '"projectmenu.js"'):
        assert name in js, name
    code, menu = get(web + "/ui/projectmenu.js")
    assert code == 200
    for name in ("⋯", "Delete project…", "Stops its work, archives its ", " chats, removes eki's worktrees. ",
                 "Your folder ", " is not touched.", '"no folder"', 'p.id === "eki" ? ""', 'method: "DELETE"',
                 '"X-Eki": "1"', '"/api/projects/" + encodeURIComponent(p.id)', ">Cancel<", ">Delete<", "#pm-err"):
        assert name in menu, name
    assert ".pm-more" in get(web + "/ui/pages.css")[1]


def test_the_project_api_answers(web):
    got = json.loads(get(web + "/api/projects")[1])
    assert got["projects"][0]["id"] == "eki" and "user_home" in got
    one = json.loads(get(web + "/api/projects/eki")[1])
    assert set(one["sections"]) == {"chats", "background", "pictures", "files"}


# nav.js needs a page; this stub is just enough to load it, register views and dispatch an address.
STUB = """
const els = {};
const el = (id) => els[id] || (els[id] = { id, dataset: {}, textContent: "", classList: { toggle() {} } });
global.navigator = { userAgent: "" };
global.location = { hash: process.argv[3] };
global.window = { addEventListener() {}, dispatchEvent() {} };
global.CustomEvent = function () {};
global.ResizeObserver = function () { this.observe = () => {}; };
global.requestAnimationFrame = () => 0;
global.document = { documentElement: { dataset: {} }, getElementById: el, addEventListener() {}, querySelectorAll: () => [] };
require(process.argv[2]);
const seen = [];
for (const v of ["projects", "project", "artifacts"]) window.nav.register(v, { show: (a) => seen.push([v, a == null ? null : a]), hide() {} });
window.nav.thread((id) => seen.push(["chat", id]));
window.nav.start();
process.stdout.write(JSON.stringify({ seen, view: els.app.dataset.view }));
"""


def dispatch(tmp_path, hash_):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "nav.js"), hash_], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("hash_, want", [
    ("#projects", {"seen": [["projects", None]], "view": "projects"}),
    ("#project/ab12cd", {"seen": [["project", "ab12cd"]], "view": "project"}),
    ("#project/eki", {"seen": [["project", "eki"]], "view": "project"}),
    ("#pictures", {"seen": [["artifacts", "picture"]], "view": "artifacts"}),   # the old address: Artifacts, Pictures
    ("#f00dfeed", {"seen": [["chat", "f00dfeed"]], "view": "chat"}),
    ("", {"seen": [["chat", None]], "view": "chat"}),
])
def test_nav_routes_projects(tmp_path, hash_, want):
    assert dispatch(tmp_path, hash_) == want


def test_the_project_header_links_its_workflow_page(web):
    js = get(web + "/ui/project.js")[1]
    assert 'href="#project/${encodeURIComponent(p.id)}/workflow">Workflow</a>' in js
    page = get(web + "/")[1]
    order = [page.index(f"/ui/{f}") for f in ("project.js", "workflowcanvas.js", "workflowinspect.js", "workflowpage.js")]
    assert order == sorted(order)
    code, wf = get(web + "/ui/workflowpage.js")
    assert code == 200 and '"/workflow"' in wf and "No workflow card: these are the defaults." in wf
    assert get(web + "/ui/workflowpage.css")[0] == 200


# project.js on a page of stub elements: #project/p1 loads /api/projects/p1 (input.project), and a fresh project
# also /api/projects/p1/starter (input.starter); input.add clicks the first Add, input.ask sends the ask box.
PAGE = r"""
const input = JSON.parse(process.argv[3]);
const els = {};
let view = null;
const mk = (id) => ({ id, dataset: {}, value: "", innerHTML: "", textContent: "", hidden: false, placeholder: "",
  parentNode: null, l: {}, classList: { toggle() {} },
  addEventListener(t, f) { this.l[t] = f; }, appendChild(c) { c.parentNode = this; }, insertBefore() {}, focus() {} });
global.window = { addEventListener() {} };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
global.document = { addEventListener() {}, querySelector: () => true, head: mk("head"),
  createElement: () => (view = mk("project-view")),
  getElementById: (id) => id === "project-view" ? null : (els[id] || (els[id] = mk(id))) };
global.setInterval = () => 0;
const calls = [], drafts = [], went = [];
window.ekiDrafts = { bind: (t, k) => drafts.push(["bind", k]), clear: (k) => drafts.push(["clear", k]), flush() {} };
global.nav = { register() {}, go: (h) => went.push(h) };
global.fetch = async (url, opts) => {
  calls.push([url, opts && opts.body ? JSON.parse(opts.body) : null]);
  const d = url === "/api/projects/p1" ? input.project : url.endsWith("/starter") ? { suggestions: input.starter || [] }
    : url === "/api/ask" ? { thread: "t9" } : {};
  return { ok: true, json: async () => d };
};
require(process.argv[2]);
const tick = () => new Promise((r) => setTimeout(r, 10));
(async () => {
  window.projects.show("p1");
  await tick(); await tick();
  const out = {};
  if (input.add) {
    const b = { disabled: false, dataset: { act: "starter", i: "0" } };
    await view.l.click({ target: { closest: (s) => (s === "[data-act]" ? b : null) } });
  }
  if (input.ask) {
    els["pj-ask-text"].value = "make a title screen";
    await els["pj-ask"].l.submit({ preventDefault() {} });
  }
  Object.assign(out, { calls, drafts, went, startHidden: els["pj-start"].hidden, list: (els["pj-start-list"] || {}).innerHTML,
    placeholder: els["pj-ask-text"].placeholder, askIn: (els["pj-ask"].parentNode || {}).id });
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""

FRESH = {"id": "p1", "name": "beat-box", "folder": "/tmp/beat-box", "folder_ok": True, "chats": 0, "goals": 0, "prs": 0,
         "sections": {"chats": [], "background": {"goals": [], "standing": [], "prs": []}, "pictures": {"items": []}, "files": []}}
SUGGEST = [{"template": "instructions", "from": "README.md", "why": "README.md says what this is"},
           {"template": "serve", "from": "package.json", "why": "package.json has a dev script"}]


def page(tmp_path, data):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "page.js"
    stub.write_text(PAGE)
    out = subprocess.run(["node", str(stub), str(WEB / "project.js"), json.dumps(data)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_fresh_project_shows_start_here_with_the_suggestions(tmp_path):
    got = page(tmp_path, {"project": FRESH, "starter": SUGGEST})
    assert not got["startHidden"] and got["askIn"] == "pj-start-ask"
    assert got["placeholder"] == "Ask for the first thing"
    assert ["/api/projects/p1/starter", None] in got["calls"]
    assert "Instructions card" in got["list"] and "README.md says what this is" in got["list"]
    assert got["list"].count(">Add</button>") == 2
    assert ["bind", "new:p1"] in got["drafts"]
    assert "Start here" in (WEB / "project.js").read_text()


def test_add_posts_the_template_and_shows_it_added(tmp_path):
    got = page(tmp_path, {"project": FRESH, "starter": SUGGEST, "add": True})
    assert ["/api/projects/p1/cards", {"template": "instructions"}] in got["calls"]
    assert "Added ✓" in got["list"] and got["list"].count(">Add</button>") == 1


def test_the_panel_goes_once_there_are_chats(tmp_path):
    p = dict(FRESH, chats=1, sections=dict(FRESH["sections"], chats=[{"id": "t1", "title": "first", "updated": 0}]))
    got = page(tmp_path, {"project": p, "starter": SUGGEST})
    assert got["startHidden"] and got["askIn"] == "pj-ask-home" and got["placeholder"] == "New chat here…"
    assert not any(c[0].endswith("/starter") for c in got["calls"])


def test_sending_the_first_ask_clears_its_draft(tmp_path):
    got = page(tmp_path, {"project": FRESH, "ask": True})
    assert ["/api/ask", {"prompt": "make a title screen", "project": "p1"}] in got["calls"]
    assert ["clear", "new:p1"] in got["drafts"] and got["went"] == ["t9"]
