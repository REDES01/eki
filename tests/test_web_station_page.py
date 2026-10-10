"""The Station page: its script and style are served, and the script names the API it reads and acts on."""
import threading
import urllib.request

import pytest

from eki import server


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def test_station_files_are_served(web):
    assert get(web + "/ui/station.css")[0] == 200
    code, js = get(web + "/ui/station.js")
    assert code == 200
    for name in ("/api/station", "/api/builds", "/api/journal", "/api/digests", "/api/route", "/api/self",
                 "/api/self/release", "/api/self/autonomy", "/api/digests/write", "nav.register", "cards.render",
                 "window.station"):
        assert name in js, name


def test_digest_summary_shows_the_headline_and_links_long(web):
    js = get(web + "/ui/station.js")[1]
    body = js[js.index("function digests(d)"):js.index("async function part(")]
    assert "d.headline" in body.split("\n")[1]      # the summary line
    assert "encodeURIComponent(d.long)" in body and "/api/file?path=" in body


def test_station_actions_send_the_header(web):
    js = get(web + "/ui/station.js")[1]
    assert '"X-Eki": "1"' in js
    assert "{ yes: true }" in js          # a locked item's apply carries the person's yes


def test_page_loads_station(web):
    page = get(web + "/")[1]
    assert '<script src="/ui/station.js">' in page and 'href="/ui/station.css"' in page


def test_a_row_target_object_shows_as_its_key():
    """An object target in routing.json reads `claude:opus@high`, not [object Object]."""
    import json
    import pathlib
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    js = (pathlib.Path(server.__file__).parent / "web" / "station.js").read_text()
    fn = re.search(r"const targetName = .*?;\n", js, re.S).group(0)
    cases = ["claude", {"provider": "claude", "model": "opus", "effort": "high"}, {"provider": "codex"}]
    out = subprocess.run([node, "-e", fn + f"console.log(JSON.stringify({json.dumps(cases)}.map(targetName)))"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["claude", "claude:opus@high", "codex"]
