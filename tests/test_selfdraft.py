import json

import pytest

from eki import paths, selfdraft, selfwork, store, workspace
from conftest import run_inline
from test_self import src, says  # noqa: F401 — the source fixture and what the fake says

DRAFTED = """I read the design and the code.

GOAL:
What must be true when done: eki/a.py exists.
Read first: eki/x.py.
DRAFT: done
"""


def goal(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def set_planner(**want):
    rp = paths.config("routing")
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "self": {"planner": want}}))


def test_a_wish_is_drafted_first_pinned_to_the_code_rows_first_choice(conn, src):
    gid = selfwork.submit(conn, "make eki nicer", draft=True)
    g = goal(conn, gid)
    assert g["state"] == "drafting" and g["wish"] == "make eki nicer" and g["plan_run"] is None
    run = store.run(conn, g["draft_run"])
    assert store.cwd_of(conn, run["thread_id"]).endswith(f"draft-{gid}")
    assert run["provider"] == "fake" and run["pinned"] and run["model"] is None
    assert "make eki nicer" in run["prompt"] and run["priority"] == "now"
    assert selfwork.tick(conn) == []                              # still running: nothing moves


def test_as_is_goes_straight_to_planning_pinned_to_the_planner(conn, src):
    set_planner(provider="fake2", model="opus")
    gid = selfwork.submit(conn, "exactly this", owner="eki")
    g = goal(conn, gid)
    run = store.run(conn, g["plan_run"])
    assert g["state"] == "planning" and g["wish"] == "exactly this" and g["draft_run"] is None
    assert (run["provider"], run["model"], run["priority"]) == ("fake2", "opus", "background")


def test_a_drafted_goal_is_planned(conn, src, tmp_path, monkeypatch):
    set_planner(provider="fake2", model="opus")
    gid = selfwork.submit(conn, "a wish", draft=True)
    draft = store.run(conn, goal(conn, gid)["draft_run"])
    assert (draft["provider"], draft["model"]) == ("fake2", "opus")
    says(tmp_path, monkeypatch, DRAFTED)
    run_inline(conn, draft["id"])
    said = selfwork.tick(conn)
    assert f"goal {gid}: drafted → planning" in said
    g = goal(conn, gid)
    assert g["state"] == "planning" and g["drafted_at"] and g["wish"] == "a wish"
    assert g["text"] == "What must be true when done: eki/a.py exists.\nRead first: eki/x.py."
    plan = store.run(conn, g["plan_run"])
    assert g["text"] in plan["prompt"] and (plan["provider"], plan["model"]) == ("fake2", "opus")
    assert store.cwd_of(conn, plan["thread_id"]).endswith(f"plan-{gid}")
    assert not workspace.path_for(f"draft-{gid}").exists()
    assert not workspace.git(selfwork.repo(), "branch", "--list", f"eki/draft-{gid}")
    assert selfdraft.conclude(conn, g) == [] and selfwork.tick(conn) == []     # once only
    assert goal(conn, gid)["plan_run"] == g["plan_run"]


@pytest.mark.parametrize("answer, why", [
    ("GOAL:\nsomething\nDRAFT: person too vague\n", "too vague"),
    ("I couldn't decide.\n", "no GOAL"),
])
def test_no_goal_leaves_it_for_you(conn, src, tmp_path, monkeypatch, answer, why):
    gid = selfwork.submit(conn, "hmm", draft=True)
    says(tmp_path, monkeypatch, answer)
    run_inline(conn, goal(conn, gid)["draft_run"])
    said = selfwork.tick(conn)
    g = goal(conn, gid)
    assert g["state"] == "left" and why in g["error"] and g["plan_run"] is None
    assert any("left" in s for s in said)
    assert not workspace.path_for(f"draft-{gid}").exists()
    assert selfdraft.conclude(conn, g) == [] and goal(conn, gid)["state"] == "left"


def test_a_failed_draft_run_fails_the_goal(conn, src):
    gid = selfwork.submit(conn, "please fail", draft=True)     # the fake's `fail` word
    run = run_inline(conn, goal(conn, gid)["draft_run"])
    assert run["state"] == "failed"
    selfwork.tick(conn)
    g = goal(conn, gid)
    assert g["state"] == "failed" and "the draft run ended failed" in g["error"]
    assert selfdraft.conclude(conn, g) == [] and goal(conn, gid)["state"] == "failed"


def test_planner_defaults(home):
    assert selfdraft.planner() == ("fake", None)
    set_planner(model="opus")
    assert selfdraft.planner() == ("fake", "opus")
