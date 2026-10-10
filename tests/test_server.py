"""The server and a page that hangs up before it is answered."""
import socket
import struct
import threading

import pytest

from eki import api_models, server


@pytest.fixture
def srv(home):
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def test_a_page_that_hangs_up_is_not_a_fault(srv, monkeypatch):
    asked, answer, done = threading.Event(), threading.Event(), threading.Event()
    faults = []

    def slow_read(conn):                    # the page reloads while the lists are read
        asked.set()
        answer.wait(10)
        return {"providers": {"x": "y" * 4_000_000}}

    monkeypatch.setattr(api_models, "read", slow_read)
    monkeypatch.setattr(server.observe, "fault", lambda *a, **k: faults.append(a))
    finish = server.Handler.finish

    def finished(self):
        try:
            finish(self)
        finally:
            done.set()
    monkeypatch.setattr(server.Handler, "finish", finished)

    c = socket.create_connection(srv.server_address)
    c.sendall(b"GET /api/models HTTP/1.1\r\nHost: x\r\n\r\n")
    assert asked.wait(10)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # hang up hard
    c.close()
    answer.set()
    assert done.wait(10)
    assert faults == []


def _req(srv, method, path, body=None, eki=True):
    import json
    import urllib.error
    import urllib.request
    host, port = srv.server_address[:2]
    req = urllib.request.Request(f"http://{host}:{port}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode())
    if eki:
        req.add_header("X-Eki", "1")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _chat(title="a chat"):
    from eki import db, store
    conn = db.connect()
    try:
        return store.create_thread(conn, title, None)
    finally:
        conn.close()


def test_delete_a_thread_needs_the_header(srv):
    tid = _chat()
    assert _req(srv, "DELETE", f"/api/threads/{tid}", eki=False)[0] == 403
    assert tid in [t["id"] for t in _req(srv, "GET", "/api/threads")[1]]
    assert _req(srv, "DELETE", f"/api/threads/{tid}") == (200, {"deleted": True, "stopping": 0})
    assert tid not in [t["id"] for t in _req(srv, "GET", "/api/threads")[1]]
    assert _req(srv, "DELETE", f"/api/threads/{tid}")[0] == 404


def test_pin_returns_the_entry(srv):
    tid = _chat()
    code, got = _req(srv, "POST", f"/api/threads/{tid}", {"pin": True})
    assert code == 200 and got["id"] == tid and got["pinned"] is True
    assert _req(srv, "POST", f"/api/threads/{tid}", {"title": "  "})[0] == 400
    assert _req(srv, "POST", "/api/threads/nope", {"pin": True})[0] == 404


def test_groups_are_made_and_listed(srv):
    code, g = _req(srv, "POST", "/api/groups", {"name": "Work"})
    assert code == 200 and g["name"] == "Work"
    assert _req(srv, "GET", "/api/groups") == (200, [g])


def test_archived_lists_only_archived(srv):
    kept, gone = _chat("kept"), _chat("gone")
    code, got = _req(srv, "POST", f"/api/threads/{gone}", {"archive": True})
    assert code == 200 and got["archived"] is True
    assert [t["id"] for t in _req(srv, "GET", "/api/threads?archived=1")[1]] == [gone]
    assert [t["id"] for t in _req(srv, "GET", "/api/threads")[1]] == [kept]
