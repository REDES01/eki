"""The project pages: #projects and #project/<id> route to project.js, which reads and acts on /api/projects."""
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
    assert page.index("/ui/nav.js") < page.index("/ui/project.js") < page.index("/ui/app.js")


def test_project_js_names_the_api_and_the_page_parts(web):
    code, js = get(web + "/ui/project.js")
    assert code == 200
    for name in ('"/api/projects"', '"/api/projects/"', '"/folder"', '"/clone"', '"/instructions"', '"/api/ask"',
                 "project: S.id", '"X-Eki": "1"', "data-file", "panel.show", 'nav.register("projects"',
                 'nav.register("project"', "New chat here", "Have an agent write instructions", "Clone to ~/",
                 "cloning", "your real folder.", "Background: ", "New project", "} open`", "data-sect"):
        assert name in js, name
    css = get(web + "/ui/style.css")[1]
    assert "#project-view" in css and ".pj-sect.open .pj-fold" in css


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
for (const v of ["projects", "project", "pictures"]) window.nav.register(v, { show: (a) => seen.push([v, a == null ? null : a]), hide() {} });
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
    ("#pictures", {"seen": [["pictures", None]], "view": "pictures"}),
    ("#f00dfeed", {"seen": [["chat", "f00dfeed"]], "view": "chat"}),
    ("", {"seen": [["chat", None]], "view": "chat"}),
])
def test_nav_routes_projects(tmp_path, hash_, want):
    assert dispatch(tmp_path, hash_) == want
