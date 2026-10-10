"""The route table: a page's api_*.ROUTES answers through the server, and the old rules hold."""
import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from eki import api_artifacts, api_customize, api_schedule, apiroutes, db, paths, server


@pytest.fixture
def srv(home):
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


def call(url, body=None, eki=True):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Eki": "1"} if eki else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_a_get_route_answers_with_match_and_query(srv, monkeypatch):
    def echo(conn, m, q, body):
        assert isinstance(conn, sqlite3.Connection)
        return {"name": m.group(1), "q": q, "body": body}
    monkeypatch.setattr(api_artifacts, "ROUTES", [("GET", r"/api/echo/(\w+)", echo)])
    assert call(srv + "/api/echo/abc?x=1&x=2&y=z") == (200, {"name": "abc", "q": {"x": "1", "y": "z"}, "body": {}})


def test_a_post_route_gets_the_body(srv, monkeypatch):
    monkeypatch.setattr(api_schedule, "ROUTES", [("POST", r"/api/schedule", lambda c, m, q, b: {"got": b})])
    assert call(srv + "/api/schedule", {"text": "hi"}) == (200, {"got": {"text": "hi"}})
    assert call(srv + "/api/schedule")[0] == 404           # a GET isn't the POST's route


def test_errors_map_to_400_and_404(srv, monkeypatch):
    def bad(c, m, q, b):
        raise ValueError("no good")

    def missing(c, m, q, b):
        raise KeyError("no such thing")
    monkeypatch.setattr(api_customize, "ROUTES", [("GET", r"/api/bad", bad), ("GET", r"/api/missing", missing)])
    assert call(srv + "/api/bad") == (400, {"error": "no good"})
    assert call(srv + "/api/missing") == (404, {"error": "no such thing"})


def test_unknown_paths_are_404_and_a_post_needs_x_eki(srv, monkeypatch):
    monkeypatch.setattr(api_customize, "ROUTES", [("POST", r"/api/skills", lambda c, m, q, b: {})])
    assert call(srv + "/api/nothing-here")[0] == 404
    assert call(srv + "/api/nothing-here", {})[0] == 404
    assert call(srv + "/api/skills", {}, eki=False)[0] == 403
    assert call(srv + "/api/skills", {}) == (200, {})


def test_match_is_whole_path_and_by_method(monkeypatch):
    fn = lambda c, m, q, b: None                            # noqa: E731
    monkeypatch.setattr(api_artifacts, "ROUTES", [("GET", r"/api/artifacts", fn)])
    assert apiroutes.match("GET", "/api/artifacts")[0] is fn
    assert apiroutes.match("GET", "/api/artifacts/x") is None
    assert apiroutes.match("POST", "/api/artifacts") is None


def test_an_old_db_gets_the_new_columns_and_table(home):
    old = sqlite3.connect(paths.db())
    old.executescript("""
        CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', provider TEXT,
                              cwd TEXT, created_at REAL NOT NULL);
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                               branch TEXT NOT NULL, check_cmd TEXT, created_at REAL NOT NULL);
    """)
    old.close()
    conn = db.connect()
    cols = lambda t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}   # noqa: E731
    assert "schedule" in cols("threads")
    assert "description" in cols("projects")
    assert {"id", "text", "project", "cadence", "tz", "target", "state", "next_at", "last_at",
            "last_thread", "why", "created_at"} <= cols("schedules")
    conn.execute("INSERT INTO schedules (id, text, cadence, tz, created_at) VALUES ('s', 't', '{}', 'UTC', 0)")
    assert conn.execute("SELECT state FROM schedules").fetchone()["state"] == "on"


def test_a_delete_reaches_the_route_table(srv, monkeypatch):
    monkeypatch.setattr(api_customize, "ROUTES", [("DELETE", r"/api/things/(\w+)",
                                                    lambda c, m, q, b: {"gone": m.group(1)})])
    req = urllib.request.Request(srv + "/api/things/abc", method="DELETE", headers={"X-Eki": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert json.loads(r.read()) == {"gone": "abc"}
    req = urllib.request.Request(srv + "/api/things/abc", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("no 403")
    except urllib.error.HTTPError as e:
        assert e.code == 403
