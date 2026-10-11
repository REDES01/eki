"""A picture card's part in an open card: cardpicture.js (and its stylesheet) is served, loaded by
projectcardpanel.js, registered for the service kind, and names what it draws and the routes it uses."""
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


def test_its_files_are_served(web):
    for f in ("cardpicture.js", "cardpicture.css"):
        code, body = get(web + "/ui/" + f)
        assert code == 200 and body.strip(), f


def test_the_panel_loads_it(web):
    assert '"cardpicture.js"' in get(web + "/ui/projectcardpanel.js")[1]
    assert len((WEB / "projectcardpanel.js").read_text().splitlines()) <= 400


def test_it_registers_for_service_and_names_what_it_draws(web):
    js = get(web + "/ui/cardpicture.js")[1]
    for name in ('window.projectCardKinds.service = { title: "Pictures", keys: ["target", "out"], draw, close }',
                 "card.picture", "settingsTarget.html(", "settingsTarget.value(", '"/api/models"',
                 '.kind === "comfyui"', "{ target: v }", "set.provider = null", "{ out: ", '"/run"', '"/uses/"',
                 '"--prompt"', '"--" + f.dataset.input', "/api/file?path=", "by hand", "not found",
                 "pic.recent", "title.hidden = !pic", "cardpicture.css", "settingstarget.js"):
        assert name in js, name
    assert len((WEB / "cardpicture.js").read_text().splitlines()) < 400


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_it_parses():
    p = subprocess.run(["node", "--check", str(WEB / "cardpicture.js")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
