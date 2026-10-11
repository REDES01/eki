"""The engine follows a project's workflow card: its plan and the gates after a build (eki/workflowgates.py)."""
import json

import pytest

from eki import projects, selfwork, store, workflowgates
from conftest import run_inline
from test_project_goals import built, planned, says, src  # noqa: F401 — the source fixture
from test_review import settle
from test_workflow_card import card

#: the fake stands in for the local model on the review chore
pytestmark = pytest.mark.usefixtures("text_fake")
#: the real reader of eki's own card (the home fixture hides this checkout's card from every test)
OWN = workflowgates.own


def commands(conn):
    return conn.execute("SELECT * FROM runs WHERE provider='command'").fetchall()


def test_self_only_proposes_with_no_gate_run_and_no_review(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self"])
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "true")
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "proposed" and it["verdict"] == "checks: self only"
    assert it["review_run"] is None and it["review"] == "none: not in this project's workflow"
    assert it["judged_by"] is None and not commands(conn)


def test_command_is_gate_one_and_no_reviewer_without_review(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command="echo judged")
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "false")     # the card's command, not the row's
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["judged_by"] == "echo judged"
    assert json.loads(store.run(conn, it["run_id"])["prompt"]) == ["/bin/sh", "-c", "echo judged", "eki-judge"]
    assert it["review_run"] is None and it["review"] == "none: not in this project's workflow"
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"


def test_review_alone_starts_the_reader_and_judges_by_it(conn, src, proj, tmp_path, monkeypatch):
    settle(local="bare")                   # a local model to read with
    card(proj, checks=["self", "review"])
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "false")
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["review_run"]
    gate = json.loads(store.run(conn, it["run_id"])["prompt"])
    assert gate[:2] == ["/bin/sh", "-c"] and gate[2] == "echo 'checks: self, review'"
    run_inline(conn, it["run_id"])
    says(tmp_path, monkeypatch, "Looked.\nREVIEW: ok\n")
    run_inline(conn, it["review_run"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed" and it["review"] == "ok"


def test_playtester_plays_the_change_after_the_command(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command", "playtester"], command="make test || exit 1")
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built(conn, tmp_path, monkeypatch, it)
    line = json.loads(store.run(conn, it["run_id"])["prompt"])[2]
    assert line == "( make test || exit 1\n) && eki use playtester 'play the change: Add greet'"


def test_plan_single_is_one_item_with_no_planner_and_says_its_checks_once(conn, src, proj, tmp_path, monkeypatch):
    card(proj, plan="single", checks=["self"])
    pid = projects.add(conn, proj, check=None)
    gid = selfwork.submit(conn, "add a greeting\n\nmake greet.py say hi", project=pid)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert g["state"] == "planned" and g["plan_run"] is None
    items = conn.execute("SELECT * FROM items WHERE goal_id=?", (gid,)).fetchall()
    assert len(items) == 1 and items[0]["spec"] == "add a greeting\n\nmake greet.py say hi"
    said = selfwork.tick(conn)
    assert [s for s in said if "building" in s] == [
        f"item {items[0]['id']}: building (add a greeting) — plan single; checks: self only"]
    assert not any("checks:" in s for s in selfwork.tick(conn))


def test_a_split_plan_says_its_checks_when_it_concludes(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self"])
    seen = []
    real = selfwork.tick
    monkeypatch.setattr(selfwork, "tick", lambda c: seen.extend(real(c)) or seen)
    planned(conn, tmp_path, monkeypatch, proj, None)
    assert any(s.endswith("1 item(s) planned — checks: self only") for s in seen)
    assert not any("checks:" in s for s in seen if "building" in s)


def test_eki_settings_follow_its_own_card(conn, src, monkeypatch):
    monkeypatch.setattr(workflowgates, "own", OWN)
    assert selfwork.settings()["autonomy"] == "propose"            # no card: today's settings
    card(src, parallel=2, merge="auto", checks=["self", "batch", "train"])
    st = selfwork.settings()
    assert (st["autonomy"], st["parallel"], st["review"]) == ("apply", 2, False)
