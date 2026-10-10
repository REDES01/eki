"""nav.js sends each address to its view: the new pages, Customize's tabs and the old #pictures."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# nav.js needs a page; this stub is just enough to load it and call route() and dispatch().
STUB = """
const links = ["chat", "projects", "pictures", "artifacts", "station"].map((id) => ({
  id, classList: { on: false, toggle(_, v) { this.on = v; } } }));
const el = () => ({ dataset: {}, textContent: "" });
const app = el();
global.navigator = { userAgent: "node" };
global.location = { hash: "" };
global.window = { addEventListener() {}, dispatchEvent() {} };
global.CustomEvent = function () {};
global.requestAnimationFrame = () => 0;
global.ResizeObserver = function () { this.observe = () => {}; };
global.document = { documentElement: { dataset: {} }, getElementById: (id) => id === "app" ? app : el(),
                    addEventListener() {}, querySelectorAll: (q) => q === "#side .side-link" ? links : [] };
require(process.argv[2]);
const [names, views, hash] = JSON.parse(process.argv[3]);
const shown = [];
for (const v of views) window.nav.register(v, { show: (arg) => shown.push([v, arg]), hide() {} });
location.hash = "#" + hash;
window.nav.start();
process.stdout.write(JSON.stringify({ routes: names.map((n) => window.nav.route(n)), shown,
  lit: links.filter((a) => a.classList.on).map((a) => a.id) }));
"""


def nav(tmp_path, names=(), views=(), hash=""):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "nav.js"), json.dumps([list(names), list(views), hash])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_new_pages_route_to_their_views(tmp_path):
    got = nav(tmp_path, ["pictures", "artifacts", "scheduled", "customize", "customize/connectors",
                         "settings/models", "project/p1", "abc123"])
    assert got["routes"] == [["artifacts", "picture"], ["artifacts", None], ["scheduled", None],
                             ["customize", None], ["customize", "connectors"], ["settings", "models"],
                             ["project", "p1"], ["chat", None]]


def test_pictures_shows_artifacts_filtered_and_lights_it(tmp_path):
    got = nav(tmp_path, views=["artifacts"], hash="pictures")
    assert got["shown"] == [["artifacts", "picture"]]
    assert set(got["lit"]) == {"pictures", "artifacts"}


def test_pictures_falls_back_to_the_gallery_while_there_is_no_artifacts_view(tmp_path):
    got = nav(tmp_path, views=["pictures"], hash="pictures")
    assert got["shown"] == [["pictures", "picture"]]
