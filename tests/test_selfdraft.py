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


class Entry:
    def __init__(self, key, title):
        self.key, self.title, self.section = key, title, "Next"
        self.text = f"- [ ] {title}: more words."


ENTRIES = [Entry("aaa111", "Faster answers"), Entry("bbb222", "Quieter board")]

RANKED = """I read ROADMAP.md.

PICK: 2
WHY: x
GOAL:
What must be true when done: the board is quieter.
DRAFT: done
"""


def rank(conn, tmp_path, monkeypatch, answer):
    gid = selfdraft.open_rank(conn, ENTRIES, "roadmap: nothing else to do")
    says(tmp_path, monkeypatch, answer)
    run_inline(conn, goal(conn, gid)["draft_run"])
    return gid


def test_a_ranking_draft_plans_the_picked_entry(conn, src, tmp_path, monkeypatch):
    gid = selfdraft.open_rank(conn, ENTRIES, "roadmap: nothing else to do")
    g = goal(conn, gid)
    assert (g["source"], g["owner"], g["state"], g["pick_key"]) == ("roadmap", "eki", "drafting", None)
    assert g["why"] == "roadmap: nothing else to do"
    assert "1. roadmap:aaa111 Faster answers" in g["wish"] and "2. roadmap:bbb222 Quieter board" in g["wish"]
    run = store.run(conn, g["draft_run"])
    assert run["priority"] == "background" and run["row"] == "code" and run["provider"] == "fake"
    assert "2. [Next] Quieter board" in run["prompt"] and "PICK:" in run["prompt"]
    assert store.cwd_of(conn, run["thread_id"]).endswith(f"draft-{gid}")
    says(tmp_path, monkeypatch, RANKED)
    run_inline(conn, run["id"])
    said = selfwork.tick(conn)
    assert any("roadmap:bbb222 → planning" in s for s in said)
    g = goal(conn, gid)
    assert (g["state"], g["pick_key"], g["why"]) == ("planning", "roadmap:bbb222", "x")
    assert g["text"].startswith("What must be true when done: the board is quieter.")
    assert '"Quieter board"' in g["text"] and "depend on every other item" in g["text"]
    assert store.run(conn, g["plan_run"])["priority"] == "background"
    assert not workspace.path_for(f"draft-{gid}").exists()


def test_a_pick_out_of_range_leaves_it(conn, src, tmp_path, monkeypatch):
    gid = rank(conn, tmp_path, monkeypatch, RANKED.replace("PICK: 2", "PICK: 9"))
    selfwork.tick(conn)
    g = goal(conn, gid)
    assert (g["state"], g["pick_key"]) == ("left", "roadmap:none") and "9" in g["error"]
    assert g["plan_run"] is None and not workspace.path_for(f"draft-{gid}").exists()


def test_a_missing_pick_leaves_it(conn, src, tmp_path, monkeypatch):
    gid = rank(conn, tmp_path, monkeypatch, RANKED.replace("PICK: 2\n", ""))
    selfwork.tick(conn)
    g = goal(conn, gid)
    assert (g["state"], g["pick_key"]) == ("left", "roadmap:none") and "no PICK" in g["error"]


def test_draft_person_leaves_it_with_the_pick_recorded(conn, src, tmp_path, monkeypatch):
    answer = RANKED.replace("PICK: 2", "PICK: 1").replace("DRAFT: done", "DRAFT: person needs a design call")
    gid = rank(conn, tmp_path, monkeypatch, answer)
    selfwork.tick(conn)
    g = goal(conn, gid)
    assert (g["state"], g["pick_key"]) == ("left", "roadmap:aaa111")
    assert "needs a design call" in g["error"] and g["plan_run"] is None


def test_a_restart_between_run_and_conclude_concludes_from_the_goals_table(conn, src, tmp_path,
                                                                           monkeypatch):
    from eki import db
    gid = rank(conn, tmp_path, monkeypatch, RANKED)
    conn.close()
    fresh = db.connect()
    try:
        assert any("planning" in s for s in selfwork.tick(fresh))
        g = goal(fresh, gid)
        assert (g["state"], g["pick_key"], g["why"]) == ("planning", "roadmap:bbb222", "x")
        assert selfdraft.conclude(fresh, g) == []                        # once only
    finally:
        fresh.close()
