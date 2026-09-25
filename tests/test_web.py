import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from eki import server
from conftest import run_inline


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def post(url, body, header=True):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-Eki": "1"} if header else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_ui_files_are_served(web):
    code, body = get(web + "/")
    assert code == 200 and "<title>eki</title>" in body
    assert get(web + "/ui/app.js")[0] == 200
    with pytest.raises(urllib.error.HTTPError):
        get(web + "/ui/../server.py")


def test_ask_then_read_the_thread(web, conn):
    code, got = post(web + "/api/ask", {"prompt": "steps=1 hello from the web"})
    assert code == 200
    run_inline(conn, got["run"])
    t = json.loads(get(f"{web}/api/threads/{got['thread']}")[1])
    assert t["runs"][0]["state"] == "done" and "fake done" in t["runs"][0]["answer"]
    listed = json.loads(get(web + "/api/threads")[1])
    assert listed[0]["id"] == got["thread"]
    ev = json.loads(get(f"{web}/api/threads/{got['thread']}/events?after=0&wait=0")[1])
    assert any(e["kind"] == "text" for e in ev["events"]) and ev["active"] == 0


def test_a_post_without_the_header_is_refused(web):
    code, got = post(web + "/api/ask", {"prompt": "x"}, header=False)
    assert code == 403


def test_bad_requests_say_why(web):
    assert post(web + "/api/ask", {"prompt": ""})[0] == 400
    assert post(web + "/api/ask", {"prompt": "x", "to": "nobody"})[0] == 404


def test_providers_and_status(web):
    ps = json.loads(get(web + "/api/providers")[1])
    assert {p["name"] for p in ps} == {"fake", "fake2", "bare"}
    st = json.loads(get(web + "/api/status")[1])
    assert "running" in st and "room" in st


def test_events_wait_for_something_to_happen(web, conn):
    _, got = post(web + "/api/ask", {"prompt": "steps=1 later"})
    t0 = time.time()
    ev = json.loads(get(f"{web}/api/threads/{got['thread']}/events?after=0&wait=1")[1])
    assert ev["events"] == [] and ev["active"] == 1 and time.time() - t0 >= 0.9


def test_event_ids_are_eki_ids_not_session_ids(web, conn):
    _, got = post(web + "/api/ask", {"prompt": "steps=1 x"})
    run_inline(conn, got["run"])
    ev = json.loads(get(f"{web}/api/threads/{got['thread']}/events?after=0&wait=0")[1])["events"]
    assert all(isinstance(e["id"], int) for e in ev)
    assert get(f"{web}/api/threads/{got['thread']}/events?after=NaN&wait=0")[0] == 200
