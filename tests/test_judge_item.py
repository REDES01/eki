"""The per-item judge (eki/judge.py): the builder can't judge itself, an uncertain check gets a second
judge on another backend and then a person, and the judges' trees go when they are done with."""
import json

import pytest

from eki import judge, paths, selfwork, store, verdicts, workspace, worktreegc
from conftest import run_inline
from test_review import build, filed, gate, goal_with, item, judged, rec, rebuild, START
from test_self import src, says  # noqa: F401 — the source fixture and what the fake says


@pytest.fixture(autouse=True)
def judging(home, monkeypatch):
    monkeypatch.setattr(judge, "start_item", START)
    rp = home / "routing.json"                     # needs-you notices on, so they are written down
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "notify": {"topic": "t", "events": ["needs_you"]}}))


def third(home):
    """A third backend the code row can fall back on."""
    pp, rp = home / "providers.json", home / "routing.json"
    pp.write_text(json.dumps({**json.loads(pp.read_text()), "fake3": {"kind": "fake"}}))
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "rows": [
        {"key": "code", "title": "tools", "targets": ["fake", "fake2", "fake3"]}]}))


def notices(conn, iid):
    return conn.execute("SELECT * FROM notices WHERE key LIKE ?", (f"item:{iid}:%",)).fetchall()


def test_the_builder_cant_file_a_verdict_nor_close_a_check_by_saying_so(conn, src, tmp_path, monkeypatch):
    goal_with(conn, "add a", ["eki/a.py"])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    with pytest.raises(ValueError, match="is not a judge run"):        # build runs are never in judgings
        verdicts.submit(conn, it["run_id"], rec("c1"))
    it = build(conn, tmp_path, monkeypatch)[0]                         # it said ITEM: done, "perfect"
    it = gate(conn, it)
    assert it["state"] == "judging" and not verdicts.outcome(conn, it["review_run"])
    with pytest.raises(ValueError, match="is not a judge run"):
        verdicts.submit(conn, it["run_id"], rec("spec"))              # nor can gate 1
    assert not conn.execute("SELECT 1 FROM verdicts").fetchone()


def test_a_failed_checks_suggestion_reaches_the_rebuild_brief(conn, src, tmp_path, monkeypatch):
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1", "failed", suggestion="handle x=0"))
    assert "A judge found check c1 failed: handle x=0" in store.run(conn, it["run_id"])["prompt"]


def test_an_uncertain_check_gets_a_second_judge_elsewhere_then_you(conn, src, home, tmp_path, monkeypatch):
    third(home)
    goal_with(conn, "add a", ["eki/a.py"], carries=("c1", "c2"))
    it = build(conn, tmp_path, monkeypatch)[0]
    first = it["review_run"]
    assert store.run(conn, first)["provider"] == "fake2"
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1"),
                rec("c2", "uncertain", limitation="the docs build needs a network"))
    assert it["state"] == "judging" and it["review_run"] != first           # a second judge looks
    run = store.run(conn, it["review_run"])
    j = verdicts.judging(conn, run["id"])
    assert run["provider"] == "fake3" and json.loads(j["avoid"]) == ["fake", "fake2"]
    assert j["scope"] == "second" and json.loads(j["checks"]) == ["c2"]
    assert "Another judge could not settle" in run["prompt"] and "c2: the docs say so" in run["prompt"]
    assert not notices(conn, it["id"])
    filed(conn, tmp_path, monkeypatch, run["id"], rec("c2", "uncertain", limitation="still no network"))
    selfwork.tick(conn)
    it = item(conn, it["id"])
    assert it["state"] == "proposed" and it["review"] == "uncertain: c2: still no network"
    got = notices(conn, it["id"])
    assert len(got) == 1 and got[0]["kind"] == "needs_you" and "still no network" in got[0]["body"]
    assert not any(it["id"] in line for line in selfwork.tick(conn))     # moved once, no third judge


def test_a_second_judge_that_settles_it_proposes_ok(conn, src, home, tmp_path, monkeypatch):
    third(home)
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec", "uncertain"), rec("c1"))
    filed(conn, tmp_path, monkeypatch, it["review_run"], rec("spec"))
    selfwork.tick(conn)
    assert item(conn, it["id"])["review"] == "ok" and item(conn, it["id"])["state"] == "proposed"


def test_uncertain_with_no_other_backend_goes_straight_to_you(conn, src, tmp_path, monkeypatch):
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]                         # fake builds, fake2 judges: no third
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1", "uncertain", limitation="no screen"))
    assert it["state"] == "proposed" and it["review"] == "uncertain: c1: no screen"
    assert len(notices(conn, it["id"])) == 1
    assert conn.execute("SELECT COUNT(*) FROM judgings WHERE scope='second'").fetchone()[0] == 0


def test_a_red_gate_with_an_uncertain_judge_just_rebuilds(conn, src, home, tmp_path, monkeypatch):
    third(home)
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1", "uncertain"))
    assert it["state"] == "building" and it["review"].startswith("uncertain: c1:")
    monkeypatch.delenv("EKI_TEST_CHECK_EXIT")
    it = rebuild(conn, tmp_path, monkeypatch, it)
    assert verdicts.judging(conn, it["review_run"])["scope"] == "item"       # a fresh first judge


def test_judge_trees_go_once_the_item_is_past_judging(conn, src, tmp_path, monkeypatch):
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    tree = paths.work() / f"judge-{it['id']}"
    assert (tree / ".git").exists()
    monkeypatch.setattr(worktreegc, "GRACE", 0.0)
    assert not any("judge-" in s for s in worktreegc.tick(conn))       # judging: kept
    judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1"))
    said = worktreegc.tick(conn)
    assert f"worktree judge-{it['id']}: removed (judged, item proposed)" in said and not tree.exists()
    repo = selfwork.repo()
    assert f"eki/judge-{it['id']}" not in workspace.git(repo, "branch", "--list", "eki/*")


def test_accept_trees_go_once_the_goal_is_past_accepting(conn, src, monkeypatch):
    gid = goal_with(conn, "add a", ["eki/a.py"])
    repo = selfwork.repo()
    workspace.add(repo, f"accept-{gid}", base="main", branch=f"eki/accept-{gid}")
    monkeypatch.setattr(worktreegc, "GRACE", 0.0)
    conn.execute("UPDATE goals SET state='accepting' WHERE id=?", (gid,))
    assert not any("accept-" in s for s in worktreegc.tick(conn))
    conn.execute("UPDATE goals SET state='accepted' WHERE id=?", (gid,))
    assert f"worktree accept-{gid}: removed (accepted or not, goal accepted)" in worktreegc.tick(conn)
    assert not (paths.work() / f"accept-{gid}").exists()
