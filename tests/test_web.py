import json
import threading
import time
import urllib.error
import urllib.parse
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
    assert get(web + "/ui/mark.svg")[0] == 200
    with pytest.raises(urllib.error.HTTPError):
        get(web + "/ui/../server.py")


def test_the_window_has_a_pictures_view(web):
    _, body = get(web + "/")
    assert '<script src="/ui/gallery.js"></script>' in body and 'id="pictures"' in body and ">Pictures<" in body
    assert body.index("/ui/nav.js") < body.index("/ui/gallery.js") < body.index("/ui/app.js")   # gallery registers with nav
    code, js = get(web + "/ui/gallery.js")
    assert code == 200 and "/api/pictures" in js and "window.gallery" in js
    assert json.loads(get(web + "/api/pictures?limit=5")[1]) == {"pictures": [], "more": False}


def test_the_page_has_its_views_and_scripts_in_order(web):
    _, body = get(web + "/")
    for view in ("chat", "pictures", "station", "settings"):
        assert f'id="{view}"' in body
    assert ">Station<" in body and ">Settings<" in body
    scripts = ["md", "cards", "panel", "attach", "nav", "gallery", "station", "settings", "side", "app"]
    at = [body.index(f'<script src="/ui/{s}.js"></script>') for s in scripts]
    assert at == sorted(at)
    css = ["style", "chat", "station", "settings"]
    at = [body.index(f'<link rel="stylesheet" href="/ui/{c}.css">') for c in css]
    assert at == sorted(at) and at[-1] < body.index("</head>")
    for i in ("strip", "attach", "to", "cwd", "bg", "prompt", "send", "log", "scroll", "empty", "head", "title", "meta", "panel"):
        assert f'id="{i}"' in body
    assert body.count("data-drag") >= 3


def test_the_shell_files_are_served(web):
    code, nav = get(web + "/ui/nav.js")
    assert code == 200 and "window.nav" in nav and "hashchange" in nav and "messageHandlers.eki" in nav
    code, side = get(web + "/ui/side.js")
    assert code == 200 and "/api/threads" in side and "/api/providers" in side and "window.side" in side
    assert get(web + "/ui/chat.css")[0] == 200
    _, app = get(web + "/ui/app.js")
    assert "nav.thread(" in app and "nav.start()" in app


def test_the_chat_look_is_served(web):
    _, app = get(web + "/ui/app.js")
    assert "/api/threads/" in app and "/api/ask" in app
    assert "pmark" in app and "data-why" in app and "navigator.clipboard" in app and "execCommand" in app
    assert "cards.render" in app and "ekiAttach.thumbs" in app and "data-copy-code" in app
    code, css = get(web + "/ui/chat.css")
    assert code == 200 and "max-width: 760px" in css and ".bubble" in css and ".code-copy" in css
    _, cards = get(web + "/ui/cards.js")
    assert "window.cards = { render }" in cards and "/api/asks/${card.dataset.ask}/answer" in cards
    _, body = get(web + "/")
    for i in ("strip", "attach", "to", "cwd", "bg", "prompt", "send"):
        assert f'id="{i}"' in body


def test_the_composer_is_a_card_with_options_and_send_stop(web):
    _, body = get(web + "/")
    form = body[body.index('<form id="composer"'):body.index("</form>")]
    for i in ("strip", "attach", "attach-file", "to", "cwd", "bg", "prompt", "send", "chip", "menu"):
        assert f'id="{i}"' in form
    assert form.index('id="menu"') < form.index('id="to"') < form.index('id="cwd"') < form.index('id="bg"')
    assert 'accept="image/*"' in form
    _, app = get(web + "/ui/app.js")
    assert "/cancel" in app and "sendMode" in app and "e.submitter" in app and "shiftKey" in app
    _, css = get(web + "/ui/chat.css")
    assert "#send.stop" in css and ".menu" in css and "#main.blank" in css


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


def test_only_files_a_run_pointed_at_are_served(web, conn, home):
    import urllib.parse
    _, got = post(web + "/api/ask", {"prompt": "steps=1 write"})
    run_inline(conn, got["run"])
    t = json.loads(get(f"{web}/api/threads/{got['thread']}")[1])
    made = t["runs"][0]["files"]
    assert made and made[0].endswith("made.html")
    code, body = get(web + "/api/file?path=" + urllib.parse.quote(made[0]))
    assert code == 200 and "made by fake" in body
    secret = home / "providers.json"
    with pytest.raises(urllib.error.HTTPError):
        get(web + "/api/file?path=" + urllib.parse.quote(str(secret)))


def test_answering_an_ask_over_http(web, conn):
    from eki import asks, db, store
    with db.tx(conn):
        tid = store.create_thread(conn, "t", None)
        rid = store.create_run(conn, tid, "x")
    aid = asks.create(conn, rid, tid, "permission", {"tool": "Bash", "detail": "ls"})
    assert json.loads(get(web + "/api/asks")[1])[0]["id"] == aid
    code, got = post(f"{web}/api/asks/{aid}/answer", {"allow": False})
    assert code == 200 and got["result"] == "answered"
    assert json.loads(get(web + "/api/asks")[1]) == []


def test_a_run_ending_wakes_the_page_at_once(web, conn):
    """The page waits on events; the end of a run must be one, or the page shows 'working' until its poll times out."""
    _, got = post(web + "/api/ask", {"prompt": "steps=1 x"})
    after = json.loads(get(f"{web}/api/threads/{got['thread']}/events?after=0&wait=0")[1])["events"]
    last = max([e["id"] for e in after], default=0)
    run_inline(conn, got["run"])
    ev = json.loads(get(f"{web}/api/threads/{got['thread']}/events?after={last}&wait=0")[1])
    assert ev["events"][-1]["kind"] == "state" and ev["events"][-1]["state"] == "done" and ev["active"] == 0


def test_the_composer_takes_pictures(web):
    _, page = get(web + "/")
    assert '<script src="/ui/attach.js"></script>' in page
    assert page.index("/ui/attach.js") < page.index("/ui/app.js")   # app.js calls ekiAttach
    assert 'id="strip"' in page and 'id="attach"' in page and 'accept="image/*"' in page
    code, js = get(web + "/ui/attach.js")
    assert code == 200 and "window.ekiAttach" in js and "/api/attachments" in js


def test_an_uploaded_picture_goes_with_the_ask_and_shows_in_the_thread(web, conn):
    req = urllib.request.Request(web + "/api/attachments", data=b"\x89PNG\r\n\x1a\n", method="POST",
                                 headers={"X-Eki": "1", "X-Filename": "shot.png"})
    with urllib.request.urlopen(req, timeout=10) as r:
        path = json.loads(r.read())["path"]
    code, got = post(web + "/api/ask", {"prompt": "steps=1 what is this", "attachments": [path]})
    assert code == 200
    t = json.loads(get(f"{web}/api/threads/{got['thread']}")[1])
    assert t["runs"][0]["attachments"] == [path]
    with urllib.request.urlopen(web + "/api/file?path=" + urllib.parse.quote(path), timeout=10) as r:
        assert r.status == 200 and r.read().startswith(b"\x89PNG")


def test_a_port_still_held_is_tried_again(monkeypatch, conn):
    """A new engine's port may still be the old engine's for a moment; it keeps trying."""
    holder = server.serve(0)
    taken = holder.server_address[1]
    monkeypatch.setenv("EKI_PORT", str(taken))
    scheduled = []
    monkeypatch.setattr(server, "_keep_trying", lambda: scheduled.append(1))
    assert server.start_in_background() is None                 # first try: in use; a retry thread
    time.sleep(0.1)
    assert scheduled
    monkeypatch.undo()
    monkeypatch.setenv("EKI_PORT", str(taken))
    holder.server_close()
    got = server._keep_trying(patience=5.0, pause=0.05)
    assert got is not None and got.server_address[1] == taken
    got.shutdown(); got.server_close()


def test_a_port_never_freed_is_given_up_on(monkeypatch, caplog):
    holder = server.serve(0)
    monkeypatch.setenv("EKI_PORT", str(holder.server_address[1]))
    assert server._keep_trying(patience=0.2, pause=0.05) is None
    assert any("not served" in r.message for r in caplog.records)
    holder.server_close()
