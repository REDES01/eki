"""The Scheduled page's API: scheduled prompts and standing goals, through the server."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from eki import db, projectchats, server, store

DAILY = {"every": "daily", "at": "09:00"}


@pytest.fixture
def srv(home, monkeypatch):
    monkeypatch.setenv("EKI_TZ", "Asia/Tokyo")
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


def call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Eki": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_add_list_and_act(srv, conn, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    pid = projectchats.create(conn, "work", folder)
    st, got = call(srv + "/api/schedule", {"text": "Check the build\nsay what broke", "cadence": DAILY,
                                           "project": pid, "target": "fake"})
    assert st == 200
    sid = got["id"]
    st, page = call(srv + "/api/schedule")
    assert st == 200 and page["tz"] == "Asia/Tokyo"
    [row] = page["schedules"]
    assert row["id"] == sid and row["cadence"] == DAILY and row["cadence_text"] == "Daily at 09:00"
    assert row["next_at"] > db.now() and row["project_name"] == "work" and row["state"] == "on"
    assert row["target"] == "fake" and row["target_label"].startswith("fake")
    assert row["tz"] == "Asia/Tokyo" and row["last_thread"] is None

    assert call(srv + f"/api/schedule/{sid}/pause", {}) == (200, {"id": sid})
    assert call(srv + "/api/schedule")[1]["schedules"][0]["state"] == "paused"
    assert call(srv + f"/api/schedule/{sid}/resume", {})[0] == 200
    assert call(srv + "/api/schedule")[1]["schedules"][0]["state"] == "on"

    st, got = call(srv + f"/api/schedule/{sid}/run", {})
    assert st == 200 and got["id"] == sid
    t = conn.execute("SELECT * FROM threads WHERE id=?", (got["thread"],)).fetchone()
    assert t["schedule"] == sid and t["project"] == pid
    assert call(srv + "/api/schedule")[1]["schedules"][0]["last_thread"] == got["thread"]

    assert call(srv + f"/api/schedule/{sid}/drop", {})[0] == 200
    assert call(srv + "/api/schedule")[1]["schedules"] == []
    assert call(srv + "/api/schedule/nope/pause", {})[0] == 404


def test_auto_target_and_bad_input(srv):
    st, got = call(srv + "/api/schedule", {"text": "hi", "cadence": {"every": "hourly", "minute": 5}})
    assert st == 200
    row = call(srv + "/api/schedule")[1]["schedules"][0]
    assert row["target"] is None and row["target_label"] == "Auto" and row["project_name"] is None
    assert row["cadence_text"] == "Hourly at :05"
    assert call(srv + "/api/schedule", {"text": "hi", "cadence": {"every": "daily", "at": "25:00"}})[0] == 400
    assert call(srv + "/api/schedule", {"text": "hi", "cadence": {"every": "monthly"}})[0] == 400
    assert call(srv + "/api/schedule", {"text": "hi"})[0] == 400
    assert call(srv + "/api/schedule", {"text": "", "cadence": DAILY})[0] == 400
    assert call(srv + "/api/schedule", {"text": "hi", "cadence": DAILY, "project": "nowhere"})[0] == 400
    assert call(srv + "/api/schedule", {"text": "hi", "cadence": DAILY, "target": "nobody:x"})[0] == 400


def _standing(conn, text, project=None, state="on"):
    sid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO standing(id, project, text, state, created_at, rest_until) "
                     "VALUES (?,?,?,?,?,?)", (sid, project, text, state, db.now(), db.now() + 3600))
    return sid


def test_standing_goals_list_and_act(srv, conn, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    pid = projectchats.create(conn, "work", folder)
    theirs = _standing(conn, "keep work tidy", project=pid)
    ours = _standing(conn, "keep eki fast")
    _standing(conn, "gone", state="dropped")
    page = call(srv + "/api/schedule")[1]
    assert [g["id"] for g in page["standing"]] == [ours, theirs]            # eki first, no dropped
    assert [g["project_name"] for g in page["standing"]] == ["eki", "work"]
    assert {"text", "state", "why", "rounds", "last_round_at"} <= set(page["standing"][0])

    assert call(srv + f"/api/standing/{ours}/pause", {}) == (200, {"id": ours})
    state = lambda: conn.execute("SELECT state, rest_until FROM standing WHERE id=?", (ours,)).fetchone()  # noqa: E731
    assert state()["state"] == "paused"
    assert call(srv + f"/api/standing/{ours}/resume", {})[0] == 200
    assert state()["state"] == "on"
    conn.execute("UPDATE standing SET rest_until=? WHERE id=?", (db.now() + 3600, ours))
    assert call(srv + f"/api/standing/{ours}/now", {})[0] == 200
    assert state()["rest_until"] <= db.now()
    assert call(srv + f"/api/standing/{ours}/drop", {})[0] == 200
    assert [g["id"] for g in call(srv + "/api/schedule")[1]["standing"]] == [theirs]
    assert call(srv + "/api/standing/nope/now", {})[0] == 404
