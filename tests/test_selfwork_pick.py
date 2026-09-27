"""What the picker builds on: its settings, and a goal that says what was picked and why."""
import sqlite3

import pytest

from eki import db, integration, paths, selfdraft, selfwork


@pytest.fixture
def quiet(monkeypatch):
    """submit without a repo or a planner: the goal row is what is under test."""
    monkeypatch.setattr(integration, "sync", lambda: "base")
    monkeypatch.setattr(selfdraft, "start_plan", lambda *a, **k: None)


def goal(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def test_the_pick_defaults_hold_when_routing_has_no_self(home):
    got = selfwork.settings()
    assert (got["loop"], got["review_max"], got["picks_per_day"], got["pick_min_cluster"]) == (False, 3, 6, 3)


def test_submit_stores_why_and_pick_key(conn, quiet):
    gid = selfwork.submit(conn, "fix it", owner="eki", source_kind="fault",
                          why="fault eki/x.py:12 KeyError, seen 2 times in 7 days", pick_key="fault:abc")
    g = goal(conn, gid)
    assert (g["pick_key"], g["why"]) == ("fault:abc", "fault eki/x.py:12 KeyError, seen 2 times in 7 days")
    assert (g["source"], g["owner"], g["state"]) == ("fault", "eki", "planning")


def test_submit_without_them_stores_null(conn, quiet):
    g = goal(conn, selfwork.submit(conn, "do a thing"))
    assert g["pick_key"] is None and g["why"] is None


def test_an_older_db_gets_both_columns_on_connect(home):
    old = sqlite3.connect(paths.db())
    old.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'ask', "
                "owner TEXT NOT NULL DEFAULT 'you', state TEXT NOT NULL DEFAULT 'planning', thread_id TEXT, "
                "plan_run TEXT, error TEXT, created_at REAL NOT NULL)")
    old.execute("INSERT INTO goals(id, text, created_at) VALUES ('g1', 'old', 0)")
    old.commit()
    old.close()
    conn = db.connect()
    have = {r["name"] for r in conn.execute("PRAGMA table_info(goals)")}
    assert {"pick_key", "why"} <= have
    assert goal(conn, "g1")["why"] is None
