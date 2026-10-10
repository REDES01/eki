"""The goal-done notice names, per item, the project branch eki landed it on."""
import time

from eki import db, notifygoals


def row(conn, table, **cols):
    with db.tx(conn):
        conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     tuple(cols.values()))


def test_the_notice_says_landed_on_master_per_item(conn):
    t = time.time()
    row(conn, "projects", id="p1", name="chapter1", path="/tmp/nowhere/p1", branch="master", created_at=t)
    row(conn, "goals", id="g1", text="tidy chapter one\nmore", state="planned", created_at=t, project="p1")
    row(conn, "items", id="i1", goal_id="g1", title="split the intro " + "x" * 80, state="applied",
        landed_on="master", created_at=t, updated_at=t)
    row(conn, "items", id="i2", goal_id="g1", title="fix the map", state="applied",
        landed_on="master", created_at=t + 1, updated_at=t + 1)
    row(conn, "items", id="i3", goal_id="g1", title="gone", state="dropped", landed_on="master",
        created_at=t + 2, updated_at=t + 2)
    g = conn.execute("SELECT * FROM goals WHERE id='g1'").fetchone()
    body = notifygoals.body_of(g, conn)
    lines = body.split("\n")
    assert lines[0] == "tidy chapter one"
    assert lines[1] == f"✓ {('split the intro ' + 'x' * 80)[:60]} — landed on master"
    assert lines[2] == "✓ fix the map — landed on master"
    assert len(lines) == 3 and body.count("landed on master") == 2
    assert notifygoals.body_of(g) == "tidy chapter one"


def test_an_eki_goal_body_is_its_first_line(conn):
    t = time.time()
    row(conn, "goals", id="g1", text="make it fast", state="planned", created_at=t)
    row(conn, "items", id="i1", goal_id="g1", title="x", state="live", created_at=t, updated_at=t)
    g = conn.execute("SELECT * FROM goals WHERE id='g1'").fetchone()
    assert notifygoals.body_of(g, conn) == "make it fast"
