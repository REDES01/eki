"""The project page's Cards area: projectcards.js (the grid, the library) and projectcardpanel.js
(an open card) are served by the window with their stylesheet, and name the cards API."""
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from eki import server

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
FILES = ("projectcards.js", "projectcardpanel.js", "projectinstructions.js", "cardsessions.js", "projectcards.css")


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def test_the_cards_files_are_served(web):
    for f in FILES:
        code, body = get(web + "/ui/" + f)
        assert code == 200 and body.strip(), f


def test_the_grid_names_its_routes_and_loads_the_rest(web):
    js = get(web + "/ui/projectcards.js")[1]
    for name in ('"/api/projects/"', '"/cards"', '"/api/cards/library"', "{ template:", "{ pool:", '"DELETE"',
                 '"X-Eki": "1"', '"Content-Type": "application/json"', "+ Add card", "confirm(", "readonly",
                 "setInterval(load, 5000)", "min ago", "projectcardpanel.js", "projectcards.css",
                 "window.projectCards = { mount, unmount }"):
        assert name in js, name


def test_the_panel_names_its_routes(web):
    js = get(web + "/ui/projectcardpanel.js")[1]
    for name in ('"/run"', '"/uses"', '"/uses/"', "{ settings:", "cardsessions.js",
                 "{ prompt:", "problems", "edit in pool", "#settings/components", '"bare"', "setTimeout(() => poll",
                 "slice(0, 20)", "window.projectCardPanel"):
        assert name in js, name
    js = get(web + "/ui/cardsessions.js")[1]
    for name in ('"/sessions"', '"/sessions/"', '"/file?name="', "emotions", "screenshots", "data_url", "tainted",
                 "← back to", "window.cardSessions"):
        assert name in js, name
    assert "pc-panel" not in get(web + "/ui/projectcards.js")[1]


def test_the_instructions_card(web):
    js = get(web + "/ui/projectinstructions.js")[1]
    for name in ('"/instructions"', '"/link-claude"', '"/read?path="', "Read first", "Link CLAUDE.md", "Edit",
                 "metaKey", "ctrlKey", "preventDefault", '"focusout"', "md.render(", "pj-err",
                 '"unlinked"', "window.projectInstructions = { open, update, close }"):
        assert name in js, name
    assert len((WEB / "projectinstructions.js").read_text().splitlines()) < 250
    assert 'data-pi="close"' not in js and "pc-pname" not in js            # the panel's header has those
    knowledge = get(web + "/ui/cardknowledge.js")[1]
    assert "ck-beside" not in knowledge and "Open in panel" not in knowledge
    grid = get(web + "/ui/projectcards.js")[1]
    for name in ("suggest", "Add instructions card", "projectinstructions.js", "lines", " missing"):
        assert name in grid, name
    assert "projectInstructions" in get(web + "/ui/projectcardpanel.js")[1]
    assert "Have an agent write instructions" not in get(web + "/ui/project.js")[1]


def test_project_js_still_mounts_it(web):
    js = get(web + "/ui/project.js")[1]
    assert 'id="pj-cards"' in js and "projectCards.mount(" in js and "projectCards.unmount()" in js
    page = get(web + "/")[1]
    assert "/ui/projectcards.js" in page


def test_small_and_valid():
    for f in FILES:
        assert len((WEB / f).read_text().splitlines()) < 400, f
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    for f in FILES[:4]:
        out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
