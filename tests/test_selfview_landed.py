"""The board says where project work landed, why its landing waits, and whose queue it joins."""
import json

import pytest

from eki import db, selfview, selfwork, store

T = 1_000_000.0


@pytest.fixture(autouse=True)
def eki_proposes(monkeypatch):
    monkeypatch.setattr(selfwork, "settings", lambda: {"autonomy": "propose", "parallel": 1})


def project(conn, pid, autonomy=None):
    with db.tx(conn):
        conn.execute("INSERT INTO projects (id, name, path, branch, created_at, autonomy) "
                     "VALUES (?,?,?,?,?,?)", (pid, "chapter1", f"/tmp/nowhere/{pid}", "master", T, autonomy))


def goal(conn, gid, pid=None):
    with db.tx(conn):
        conn.execute("INSERT INTO goals (id, text, state, created_at, project) VALUES (?,?,?,?,?)",
                     (gid, "a wish", "planned", T, pid))


def item(conn, iid, gid, state, n=0, **kw):
    cols = {"id": iid, "goal_id": gid, "title": f"item {iid}", "state": state,
            "created_at": T + n, "updated_at": T + n, **kw}
    with db.tx(conn):
        conn.execute(f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     tuple(cols.values()))
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


def test_an_item_landed_on_a_project_says_the_branch(conn):
    project(conn, "p1")
    goal(conn, "g1", "p1")
    it = item(conn, "i1", "g1", "applied", landed_on="master", rebased="a" * 40, commit_sha="b" * 40)
    assert selfview.said(it, c=conn) == f"✓ landed on master ({'a' * 12})"
    it = item(conn, "i2", "g1", "applied", landed_on="master", commit_sha="b" * 40)
    assert selfview.said(it) == f"✓ landed on master ({'b' * 12})"


def test_a_waiting_landing_says_which_file(conn):
    project(conn, "p1")
    goal(conn, "g1", "p1")
    why = "waits: chapter1 has local changes to src/x.js"
    it = item(conn, "i1", "g1", "queued", why=why)
    assert selfview.said(it, c=conn) == why
    assert why in selfview.note(conn, it)
    assert selfview.said(item(conn, "i2", "g1", "queued", why="something else")) == ""


def test_a_proposed_item_of_an_apply_project_joins_its_queue(conn):
    project(conn, "p1")                                       # NULL autonomy: apply
    goal(conn, "g1", "p1")
    it = item(conn, "i1", "g1", "proposed", touched=json.dumps(["a.js", "b.js"]))
    assert selfview.said(it, c=conn) == "✓ 2 files; joins chapter1's queue"
    assert "eki self apply" not in selfview.note(conn, it)


def test_a_propose_project_still_shows_the_apply_hint(conn):
    project(conn, "p1", autonomy="propose")
    goal(conn, "g1", "p1")
    it = item(conn, "i1", "g1", "proposed", touched=json.dumps(["a.js"]))
    assert selfview.said(it, c=conn) == "✓ 1 files; `eki self apply i1` queues it"


def test_an_eki_item_is_unchanged(conn, monkeypatch):
    goal(conn, "g1")
    it = item(conn, "i1", "g1", "proposed", touched=json.dumps(["eki/a.py"]))
    assert selfview.said(it, c=conn) == selfview.said(it) == "✓ 1 files; `eki self apply i1` queues it"
    monkeypatch.setattr(selfwork, "settings", lambda: {"autonomy": "apply", "parallel": 1})
    assert selfview.said(it, c=conn) == "✓ 1 files; joins the queue"
    it = item(conn, "i2", "g1", "applied", commit_sha="c" * 40)
    assert selfview.said(it, c=conn) == f"✓ in main ({'c' * 12})"


def test_a_batch_never_mixes_projects(conn):
    project(conn, "p1")
    goal(conn, "g1", "p1")
    goal(conn, "g2")
    rid = store.create_run(conn, store.create_thread(conn, "check", "/tmp"), "check", provider="fake")
    item(conn, "e1", "g2", "queued", 1, queued_at=T + 1, rebased="r" * 40)
    item(conn, "e2", "g2", "queued", 2, queued_at=T + 2, rebased="s" * 40, gate2_run=rid, gate2_on="s" * 40)
    p = item(conn, "p1i", "g1", "queued", 3, queued_at=T + 3, rebased="t" * 40)
    e = conn.execute("SELECT * FROM items WHERE id='e1'").fetchone()
    assert selfview.stage(conn, e) == "in the batch checked on e2"
    assert selfview.stage(conn, p) == "waiting for the next batch"
