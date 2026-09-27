import json
import time

import pytest

from eki import db, digest, engine, faults, housekeep, observe, score, selfpick

DAY = 86400.0


@pytest.fixture(autouse=True)
def fresh():
    housekeep._last_prune[0] = 0.0


def add_journal(conn, t, kind="run_end"):
    conn.execute("INSERT INTO journal(t, kind, data) VALUES (?,?,?)", (t, kind, json.dumps({})))


def kinds(conn):
    return [r["kind"] for r in conn.execute("SELECT kind FROM journal ORDER BY id")]


def not_due(monkeypatch):
    monkeypatch.setattr(digest, "due", lambda now=None: False)


def test_old_journal_rows_are_pruned_and_new_ones_kept(conn, monkeypatch):
    not_due(monkeypatch)
    now = db.now()
    add_journal(conn, now - 91 * DAY, "old")
    add_journal(conn, now - 89 * DAY, "recent")
    add_journal(conn, now, "new")
    lines = housekeep.tick(conn)
    assert kinds(conn) == ["recent", "new"]
    assert any("forgot 1 rows" in ln for ln in lines)


def test_prune_runs_at_most_once_an_hour(conn, monkeypatch):
    not_due(monkeypatch)
    calls = []
    monkeypatch.setattr(observe, "prune", lambda c, days=90: calls.append(days) or 0)
    housekeep.tick(conn)
    housekeep.tick(conn)
    assert calls == [90]
    housekeep._last_prune[0] = time.time() - housekeep.PRUNE_EVERY - 1
    housekeep.tick(conn)
    assert calls == [90, 90]


def test_a_failing_step_is_a_fault_and_the_others_still_run(conn, monkeypatch):
    not_due(monkeypatch)
    ran = []

    def boom(c, now=None):
        raise RuntimeError("score went wrong")

    monkeypatch.setattr(score, "settle", boom)
    monkeypatch.setattr(faults, "tick", lambda c, now=None: ran.append("faults") or ["opened one"])
    monkeypatch.setattr(digest, "tick", lambda c, now=None: ran.append("digest"))
    lines = housekeep.tick(conn)
    assert ran == ["faults", "digest"]
    assert "opened one" in lines
    row = conn.execute("SELECT * FROM journal WHERE kind='fault'").fetchone()
    data = json.loads(row["data"])
    assert data["where"] == "housekeep.score"
    assert "score went wrong" in data["traceback"]


def test_pick_runs_after_faults_and_a_raising_pick_is_a_fault(conn, monkeypatch):
    not_due(monkeypatch)
    names = [name for name, _ in housekeep.STEPS]
    assert names.index("pick") == names.index("faults") + 1
    ran = []

    def boom(c):
        ran.append("pick")
        raise RuntimeError("pick went wrong")

    monkeypatch.setattr(faults, "tick", lambda c, now=None: ran.append("faults") or [])
    monkeypatch.setattr(selfpick, "tick", boom)
    monkeypatch.setattr(digest, "tick", lambda c, now=None: ran.append("digest"))
    housekeep.tick(conn)
    assert ran == ["faults", "pick", "digest"]
    data = json.loads(conn.execute("SELECT data FROM journal WHERE kind='fault'").fetchone()["data"])
    assert data["where"] == "housekeep.pick" and "pick went wrong" in data["traceback"]


def test_with_the_loop_off_the_pick_step_says_nothing(conn, monkeypatch):
    not_due(monkeypatch)
    monkeypatch.setattr(faults, "tick", lambda c, now=None: [])
    housekeep.tick(conn)
    assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0
    assert not conn.execute("SELECT * FROM journal WHERE kind='fault'").fetchall()


def test_with_the_digest_due_a_page_appears(conn, monkeypatch):
    monkeypatch.setattr(digest, "due", lambda now=None: True)
    lines = housekeep.tick(conn)
    pages = list(digest.folder().glob("????-??-??.md"))
    assert len(pages) == 1
    assert f"digest written: {pages[0]}" in lines


def test_engine_tick_calls_housekeeping_on_its_duty_pass(conn, monkeypatch):
    called = []
    monkeypatch.setattr(housekeep, "tick", lambda c: called.append(c) or ["housekept"])
    monkeypatch.setattr(engine, "refresh_quota", lambda: None)
    monkeypatch.setattr(engine, "_last_self", [time.time() + 3600])
    monkeypatch.setattr(engine, "_last_duty", [0.0])
    engine.tick(conn)
    assert called == [conn]
    engine.tick(conn)
    assert len(called) == 1
