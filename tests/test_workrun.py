"""Saving the work model's state and carrying out its actions (eki/workrun.py)."""
import json
import re

import pytest

from eki import db, notify, workrun
from eki.workmodel import Event, from_label, label


def goal_with(conn, *items, project=None):
    now = db.now()
    conn.execute("INSERT INTO goals(id, text, state, created_at, project) VALUES ('g1', 'do it', 'planned', ?, ?)",
                 (now, project))
    for n, (iid, state, more) in enumerate(items):
        cols = {"id": iid, "goal_id": "g1", "title": f"item {iid}", "state": state, "created_at": now + n,
                "updated_at": now, **more}
        conn.execute(f"INSERT INTO items({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(cols.values()))


def undone(conn):
    return conn.execute("SELECT * FROM work_actions WHERE done_at IS NULL ORDER BY id").fetchall()


def test_load_reads_the_rows_and_settings(conn):
    goal_with(conn, ("a", "proposed", {"touched": '["x.py"]', "tries": 2, "review": "ok"}))
    g, items, s = workrun.load(conn, "g1")
    assert g.stage == "planned" and g.phase == "planned"
    assert items[0].stage == "proposed" and items[0].touched == ("x.py",) and items[0].tries == 2
    assert s.kind == "eki" and "bin/check" in s.locked


def test_feed_writes_the_state_and_the_actions_in_one_transaction(conn):
    goal_with(conn, ("a", "proposed", {"touched": '["bin/check"]'}))
    acts = workrun.feed(conn, "g1", "a", Event("seen", 1.0, {"queueable": True}))
    assert [a.kind for a in acts] == ["notify"]
    row = conn.execute("SELECT state, work FROM items WHERE id='a'").fetchone()
    assert row["state"] == "locked" and json.loads(row["work"])["why"] == "locked — touches bin/check; needs a person"
    saved = undone(conn)
    assert len(saved) == 1 and json.loads(saved[0]["action"])["kind"] == "notify" and saved[0]["item_id"] == "a"
    _, items, _ = workrun.load(conn, "g1")
    assert items[0].stage == "locked" and items[0].why.startswith("locked")


def test_a_step_that_fails_writes_nothing(conn, monkeypatch):
    goal_with(conn, ("a", "proposed", {}))

    def boom(*a):
        raise RuntimeError("no")
    monkeypatch.setattr(workrun.workstep, "step", boom)
    with pytest.raises(RuntimeError):
        workrun.feed(conn, "g1", "a", Event("seen", 1.0, {"in_target": True}))
    assert conn.execute("SELECT state FROM items WHERE id='a'").fetchone()[0] == "proposed"
    assert not undone(conn)


def test_a_failed_recheck_writes_nothing(conn, monkeypatch):
    goal_with(conn, ("a", "proposed", {"touched": '["bin/check"]'}))
    real = workrun.load
    calls = []

    def load(c, gid):
        got = real(c, gid)
        if not calls:                                  # a person drops it right after the first look
            c.execute("UPDATE items SET state='dropped' WHERE id='a'")
        calls.append(1)
        return got
    monkeypatch.setattr(workrun, "load", load)
    assert workrun.feed(conn, "g1", "a", Event("seen", 1.0, {"queueable": True})) == []
    assert conn.execute("SELECT state, work FROM items WHERE id='a'").fetchone()[:] == ("dropped", None)
    assert not undone(conn)


def test_a_crash_after_feed_is_carried_once_after_the_restart(conn, monkeypatch):
    goal_with(conn, ("a", "proposed", {"touched": '["bin/check"]'}))
    workrun.feed(conn, "g1", "a", Event("seen", 1.0, {"queueable": True}))
    conn.close()                                       # the engine dies here: nothing carried yet
    again = db.connect()
    monkeypatch.setattr(notify, "on", lambda kind: True)
    from eki import integration
    monkeypatch.setattr(integration, "repo", lambda: "/nowhere")     # eki's repo is never made here
    assert workrun.carry(again) == []
    assert workrun.carry(again) == []
    rows = again.execute("SELECT key, body FROM notices").fetchall()
    assert [r["key"] for r in rows] == ["item:a:locked:0"]
    assert rows[0]["body"].startswith("item a — locked, touches bin/check: eki self apply a --yes")
    assert not undone(again)


def test_carry_leaves_actions_with_no_handler_and_retries_a_failing_one(conn, monkeypatch):
    goal_with(conn, ("a", "building", {"tries": 1}))
    workrun.feed(conn, "g1", "a", Event("item_failed", 1.0, {"kind": "quota", "retry_at": 9.0}))
    workrun.feed(conn, "g1", "a", Event("item_started", 10.0, {}))    # start_run: no handler here yet
    from eki import integration
    monkeypatch.setattr(integration, "repo", lambda: "/nowhere")
    calls = []

    def flaky(c, project, action):
        calls.append(action.data["until"])
        if len(calls) == 1:
            raise OSError("disk")
        return ["held"]
    monkeypatch.setitem(workrun.HANDLERS, "hold_until", flaky)
    workrun.HANDLERS.pop("start_run", None)
    assert workrun.carry(conn) == []
    assert workrun.carry(conn) == ["held"]
    assert calls == [9.0, 9.0]
    assert [json.loads(r["action"])["kind"] for r in undone(conn)] == ["start_run"]


def test_label_round_trips_every_state_the_items_table_names():
    m = re.search(r"state TEXT NOT NULL DEFAULT 'waiting',\s*-- (.+)", db.SCHEMA)
    states = [s.strip() for s in m.group(1).split("|")]
    assert "waiting on calls" in states and "rolled back" in states
    for s in states:
        assert label(from_label("x", s)) == s
