"""The scheduler starts items across goals round-robin, compares write-sets per repo,
counts only building items against the machine cap, caps each project by its own
workflow card, and says why an item waits."""
import json

import pytest

from eki import components, db, paths, projects, scheduler, selfwork, workflowgates, workspace
from eki.cli import selfboard

OWN = workflowgates.own            # the real one: conftest stubs it out


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


def agents(n):
    p = paths.config("routing")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"machine": {"agents": n}}))


def card(root, **spec):
    where = root / "components" / "workflow"
    where.mkdir(parents=True, exist_ok=True)
    base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
    (where / "component.json").write_text(json.dumps({**base, **spec}))
    workflowgates._own.clear()


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
    agents(2)
    a1 = one(conn, "goal a", ["eki/a1.py"])
    _, a2 = add_item(conn, "a two", ["eki/a2.py"])
    b1 = one(conn, "goal b", ["eki/b1.py"])
    said = scheduler.start_ready(conn)
    assert len(said) == 2
    assert state(conn, a1)["state"] == "building" and state(conn, b1)["state"] == "building"
    assert state(conn, a1)["why"] is None
    assert tuple(state(conn, a2)) == ("waiting", "2 agents working (machine)")


def test_same_file_names_in_two_repos_both_start(conn, src, tmp_path):
    proj = _repo(tmp_path / "proj")
    pid = projects.add(conn, proj)
    mine = one(conn, "change x here", ["eki/x.py"])
    theirs = one(conn, "change x there", ["eki/x.py"], project=pid)
    scheduler.start_ready(conn)
    assert state(conn, mine)["state"] == "building"
    assert state(conn, theirs)["state"] == "building"


def test_overlapping_items_build_together(conn, src):
    first = one(conn, "change x", ["eki/x.py"])
    second = one(conn, "change x too", ["eki/x.py", "eki/y.py"])
    scheduler.start_ready(conn)
    assert state(conn, first)["state"] == "building"
    assert state(conn, second)["state"] == "building"
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


def test_judging_items_do_not_count_against_the_machine_cap(conn, src):
    agents(1)
    judged = one(conn, "being judged", ["eki/j.py"])
    selfwork._set(conn, judged, state="judging")
    other = one(conn, "another", ["eki/o.py"])
    assert scheduler.start_ready(conn)
    assert state(conn, other)["state"] == "building"
    third = one(conn, "a third", ["eki/t.py"])
    assert scheduler.start_ready(conn) == []
    assert tuple(state(conn, third)) == ("waiting", "1 agents working (machine)")


def test_the_board_shows_shape_and_why(conn, src, capsys):
    first = one(conn, "change x", ["eki/x.py"])
    second = one(conn, "change anything", [])
    gid = conn.execute("SELECT goal_id FROM items WHERE id=?", (second,)).fetchone()[0]
    conn.execute("UPDATE goals SET shape=? WHERE id=?",
                 (json.dumps({"items": 5, "depth": 2, "width": 4, "chained": 0}), gid))
    scheduler.start_ready(conn)
    selfboard.board(conn)
    out = capsys.readouterr().out
    assert "5 items · 2 deep · 4 wide" in out
    assert scheduler.NO_WRITE_SET in out


def test_the_machine_cap_holds_eki_and_a_project_together(conn, src, tmp_path):
    agents(2)
    pid = projects.add(conn, _repo(tmp_path / "proj"))
    mine = one(conn, "mine", ["eki/m.py"])
    theirs = one(conn, "theirs", ["eki/t.py"], project=pid)
    third = one(conn, "third", ["eki/z.py"], project=pid)
    scheduler.start_ready(conn)
    assert state(conn, mine)["state"] == "building" and state(conn, theirs)["state"] == "building"
    assert tuple(state(conn, third)) == ("waiting", "2 agents working (machine)")


def test_a_project_card_caps_its_own_items_under_the_machine(conn, src, tmp_path):
    agents(10)
    proj = _repo(tmp_path / "proj")
    card(proj, parallel=1)
    pid = projects.add(conn, proj, name="holo-cards")
    first = one(conn, "first", ["eki/a.py"], project=pid)
    _, second = add_item(conn, "second", ["eki/b.py"])
    scheduler.start_ready(conn)
    assert state(conn, first)["state"] == "building"
    assert tuple(state(conn, second)) == ("waiting", "1 building in holo-cards (its workflow)")


def test_eki_s_card_caps_eki_but_not_a_project(conn, src, tmp_path, monkeypatch):
    monkeypatch.setattr(workflowgates, "own", OWN)
    agents(10)
    card(src, parallel=1, checks=["self", "batch", "train"])
    assert workflowgates.own().parallel == 1
    pid = projects.add(conn, _repo(tmp_path / "proj"))
    mine = one(conn, "mine", ["eki/m.py"])
    _, mine2 = add_item(conn, "mine too", ["eki/n.py"])
    theirs = one(conn, "theirs", ["eki/t.py"], project=pid)
    _, theirs2 = add_item(conn, "theirs too", ["eki/u.py"])
    scheduler.start_ready(conn)
    assert state(conn, mine)["state"] == "building"
    assert tuple(state(conn, mine2)) == ("waiting", "1 building in eki (its workflow)")
    assert state(conn, theirs)["state"] == "building" and state(conn, theirs2)["state"] == "building"


def test_the_machine_cap_wins_over_a_larger_project_cap(conn, src, tmp_path):
    agents(1)
    proj = _repo(tmp_path / "proj")
    card(proj, parallel=5)
    pid = projects.add(conn, proj)
    first = one(conn, "first", ["eki/a.py"], project=pid)
    _, second = add_item(conn, "second", ["eki/b.py"])
    scheduler.start_ready(conn)
    assert state(conn, first)["state"] == "building"
    assert tuple(state(conn, second)) == ("waiting", "1 agents working (machine)")
