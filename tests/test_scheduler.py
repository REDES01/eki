"""The scheduler starts items across goals round-robin, compares write-sets per repo,
counts only building items against self.parallel, and says why an item waits."""
import json

import pytest

from eki import db, paths, projects, scheduler, selfwork, workspace
from eki.cli import selfboard


def _repo(r):
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "eki" / "y.py").write_text("# y\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit 0\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = _repo(tmp_path / "src")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def parallel(n):
    p = paths.config("routing")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"self": {"parallel": n}}))


def one(conn, text, files, **kw):
    gid = selfwork.submit(conn, text, plan=False, files=files, **kw)
    return conn.execute("SELECT * FROM items WHERE goal_id=?", (gid,)).fetchone()["id"]


def add_item(conn, text, files):
    """A second item in the goal of an earlier one."""
    gid = conn.execute("SELECT goal_id FROM items ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    with db.tx(conn):
        return gid, selfwork.new_item(conn, gid, text, text, files, [], False)


def state(conn, iid):
    return conn.execute("SELECT state, why FROM items WHERE id=?", (iid,)).fetchone()


def test_two_goals_take_turns(conn, src):
    parallel(2)
    a1 = one(conn, "goal a", ["eki/a1.py"])
    _, a2 = add_item(conn, "a two", ["eki/a2.py"])
    b1 = one(conn, "goal b", ["eki/b1.py"])
    said = scheduler.start_ready(conn)
    assert len(said) == 2
    assert state(conn, a1)["state"] == "building" and state(conn, b1)["state"] == "building"
    assert state(conn, a1)["why"] is None
    assert tuple(state(conn, a2)) == ("waiting", "2 building (self.parallel)")


def test_same_file_names_in_two_repos_both_start(conn, src, tmp_path):
    proj = _repo(tmp_path / "proj")
    pid = projects.add(conn, proj)
    mine = one(conn, "change x here", ["eki/x.py"])
    theirs = one(conn, "change x there", ["eki/x.py"], project=pid)
    scheduler.start_ready(conn)
    assert state(conn, mine)["state"] == "building"
    assert state(conn, theirs)["state"] == "building"


def test_an_overlapping_item_waits_naming_the_other(conn, src):
    first = one(conn, "change x", ["eki/x.py"])
    second = one(conn, "change x too", ["eki/x.py", "eki/y.py"])
    scheduler.start_ready(conn)
    assert state(conn, first)["state"] == "building"
    assert tuple(state(conn, second)) == ("waiting", f"shares eki/x.py with {first}")
    nows = one(conn, "change anything", [])
    scheduler.start_ready(conn)
    assert tuple(state(conn, nows)) == ("waiting", scheduler.NO_WRITE_SET)


def test_a_dep_says_its_title(conn, src):
    root = one(conn, "the root", ["eki/root.py"])
    gid, later = add_item(conn, "the leaf", ["eki/leaf.py"])
    selfwork._set(conn, later, deps=db.dumps([root]))
    scheduler.start_ready(conn)
    assert state(conn, root)["state"] == "building"
    assert tuple(state(conn, later)) == ("waiting", "after the root")


def test_judging_items_do_not_count_against_parallel(conn, src):
    parallel(1)
    judged = one(conn, "being judged", ["eki/j.py"])
    selfwork._set(conn, judged, state="judging")
    other = one(conn, "another", ["eki/o.py"])
    assert scheduler.start_ready(conn)
    assert state(conn, other)["state"] == "building"
    third = one(conn, "a third", ["eki/t.py"])
    assert scheduler.start_ready(conn) == []
    assert tuple(state(conn, third)) == ("waiting", "1 building (self.parallel)")


def test_the_board_shows_shape_and_why(conn, src, capsys):
    first = one(conn, "change x", ["eki/x.py"])
    second = one(conn, "change x too", ["eki/x.py"])
    gid = conn.execute("SELECT goal_id FROM items WHERE id=?", (second,)).fetchone()[0]
    conn.execute("UPDATE goals SET shape=? WHERE id=?",
                 (json.dumps({"items": 5, "depth": 2, "width": 4, "chained": 0}), gid))
    scheduler.start_ready(conn)
    selfboard.board(conn)
    out = capsys.readouterr().out
    assert "5 items · 2 deep · 4 wide" in out
    assert f"shares eki/x.py with {first}" in out
