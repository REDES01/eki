"""The doc routes over HTTP: read, save and conflict, the open queue the window polls,
POSTs refused without X-Eki, and /api/file serving a picture beside a doc."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from eki import apiroutes, paths, server


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def post(url, body, header=True):
    headers = {"Content-Type": "application/json"}
    if header:
        headers["X-Eki"] = "1"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def q(path):
    return urllib.parse.quote(str(path))


def test_routes_are_registered():
    for meth, path in (("GET", "/api/doc"), ("POST", "/api/doc"), ("POST", "/api/doc/open"),
                       ("GET", "/api/doc/opens")):
        assert apiroutes.match(meth, path)[0].__module__ == "eki.api_doc"


def test_read_save_conflict_over_http(web):
    f = paths.user_home() / "notes.md"
    f.write_text("# Notes\n")
    code, got = get(f"{web}/api/doc?path={q(f)}")
    assert code == 200 and got["text"] == "# Notes\n" and got["editable"]
    code, out = post(web + "/api/doc", {"path": str(f), "text": "# New\n", "base_hash": got["hash"]})
    assert code == 200 and out["saved"] and f.read_text() == "# New\n"
    code, out = post(web + "/api/doc", {"path": str(f), "text": "# Mine\n", "base_hash": got["hash"]})
    assert code == 200 and out["conflict"] and out["text"] == "# New\n"
    code, out = post(web + "/api/doc", {"path": str(f), "text": "# Mine\n", "base_hash": out["hash"]})
    assert out["saved"] and f.read_text() == "# Mine\n"
    assert get(f"{web}/api/doc?path={q(paths.user_home() / 'gone.md')}")[0] == 404
    assert get(f"{web}/api/doc?path=/etc/hosts")[0] == 400


def test_post_without_x_eki_is_403(web):
    f = paths.user_home() / "n.md"
    f.write_text("x\n")
    code, _ = post(web + "/api/doc", {"path": str(f), "text": "y", "base_hash": ""}, header=False)
    assert code == 403 and f.read_text() == "x\n"
    assert post(web + "/api/doc/open", {"path": str(f)}, header=False)[0] == 403


def test_open_queue_over_http(web):
    f = paths.user_home() / "AGENTS.md"
    f.write_text("a\n")
    code, start = get(web + "/api/doc/opens")
    assert code == 200 and start == {"opens": [], "last": 0}
    code, got = post(web + "/api/doc/open", {"path": str(f)})
    assert code == 200 and got["id"] > 0
    _, after = get(f"{web}/api/doc/opens?after={start['last']}")
    assert [o["id"] for o in after["opens"]] == [got["id"]] and after["opens"][0]["path"].endswith("AGENTS.md")
    assert get(web + "/api/doc/opens?after=x")[0] == 400


def test_api_file_serves_an_image_under_home(web):
    img = paths.user_home() / "pic.png"
    img.write_bytes(b"\x89PNG fake")
    with urllib.request.urlopen(f"{web}/api/file?path={q(img)}", timeout=10) as r:
        assert r.read() == b"\x89PNG fake" and r.headers["Content-Type"] == "image/png"
