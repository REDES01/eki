"""The rules of what happens next, as event sequences: no db, git or agents (eki/workstep.py)."""
import pytest

from eki import workstep, workstep_queue
from eki.workmodel import Event, Settings, goal, item, label

EKI = Settings(plan="on", gates=("check", "review", "batch"), merge="auto", parallel=4,
               locked=("bin/check", "eki/drill.py"), kind="eki")
PROJECT = Settings(plan="on", gates=("check", "review", "batch"), merge="auto", parallel=0, locked=(), kind="local")


class World:
    """A goal and its items, fed events one at a time; every action kept."""

    def __init__(self, settings, items=(), stage="planned"):
        self.s, self.goal, self.items, self.actions, self.t = settings, goal(stage), tuple(items), [], 0.0

    def feed(self, what, iid=None, at=None, **data):
        self.t = at if at is not None else self.t + 1
        if iid is not None:
            data["item"] = iid
        self.goal, self.items, acts = workstep.step(self.goal, self.items, self.s, Event(what, self.t, data))
        self.actions += acts
        return acts

    def it(self, iid):
        return next(x for x in self.items if x.id == iid)

    def label(self, iid):
        return label(self.it(iid))


def kinds(acts):
    return [(a.kind, a.data.get("role", a.data.get("gate"))) for a in acts]


def building(settings, iid="a", **more):
    w = World(settings, [item(iid, **more)])
    w.feed("item_started", iid)
    return w


def test_plan_makes_items_and_a_dependent_waits_for_its_dependency():
    w = World(EKI, stage="planning")
    w.goal = goal("drafting")
    assert kinds(w.feed("plan_ready", of="draft")) == [("start_run", "plan")]
    assert w.goal.stage == "planning" and w.goal.why == "drafted → planning"
    w.feed("plan_ready", items=[{"id": "a", "files": ["x.py"]}, {"id": "b", "deps": ["a"]}])
    assert w.goal.phase == "planned" and w.goal.why == "2 item(s) planned"
    assert [x.id for x in w.items] == ["a", "b"] and w.it("b").deps == ("a",)
    assert w.feed("item_started", "b") == () and w.it("b").why == "after a"
    acts = w.feed("item_started", "a")
    assert w.label("a") == "building" and w.it("a").tries == 1
    assert [(a.kind, a.data) for a in acts] == [("start_run", {"role": "build", "stage": "building", "tries": 1})]


def test_a_failed_plan_fails_the_goal_and_an_empty_one_says_why():
    w = World(EKI, stage="planning")
    w.feed("plan_ready", ended="failed", error="boom")
    assert (w.goal.stage, w.goal.phase, w.goal.why) == ("failed", "failed", "the plan run ended failed: boom")
    w = World(EKI, stage="planning")
    w.feed("plan_ready", items=[], why="all good")
    assert w.goal.stage == "planned" and w.goal.why == "nothing worth doing now — all good"


def test_a_goal_without_a_plan_or_under_plan_single_is_one_item():
    w = World(EKI, stage="planning")
    w.feed("goal_submitted", plan=False, item="one")
    assert w.goal.stage == "planned" and [x.id for x in w.items] == ["one"]
    w = World(Settings(plan="single"), stage="planning")
    w.feed("goal_submitted", plan=True, item="one")
    assert w.goal.stage == "planned"


def test_build_done_starts_gate_1_and_the_review_side_by_side():
    w = building(EKI)
    acts = w.feed("item_finished", "a", verdict="done", touched=["x.py", "y.py"])
    assert w.label("a") == "judging" and w.it("a").phase == "checking"
    assert kinds(acts) == [("run_gate", 1), ("start_run", "review")]
    assert w.it("a").why == "built (2 files); judging and reviewing"


def test_red_gets_one_more_try_then_the_fixer_then_unfit():
    w = building(EKI)
    w.feed("item_finished", "a", verdict="done", touched=["x.py"])
    w.feed("check_verdict", "a", result="red", review="ok")
    assert w.label("a") == "waiting" and w.it("a").why == "checks failed; one more try"
    w.feed("item_started", "a")
    w.feed("item_finished", "a", verdict="done", touched=["x.py"])
    acts = w.feed("check_verdict", "a", result="red", review="ok", tail="E assert 1 == 2")
    assert w.label("a") == "fixing" and w.it("a").fixer_used
    assert [(a.kind, a.data["role"], a.data["stage"]) for a in acts] == [("start_run", "fix", "gate1")]
    assert w.it("a").why == "gate1 failed — the fixer is on it"
    w.feed("item_finished", "a", verdict="done", touched=["x.py"])
    assert w.label("a") == "judging" and w.it("a").why.endswith("(after the fixer)")
    w.feed("check_verdict", "a", result="red", review="ok")
    assert w.label("a") == "unfit" and w.it("a").phase == "stopped"


def test_a_project_item_gets_no_fixer():
    w = building(PROJECT, tries=1)
    w.feed("item_finished", "a", verdict="done")
    w.feed("check_verdict", "a", result="red")
    assert w.label("a") == "unfit"


def test_review_no_rebuilds_once_then_proposes_with_the_objection():
    w = building(EKI)
    w.feed("item_finished", "a", verdict="done")
    w.feed("check_verdict", "a", result="green", review="no: misses the test")
    assert w.label("a") == "waiting" and w.it("a").reviews == 1
    assert w.it("a").why == "the review says no — misses the test; rebuilding once"
    w.feed("item_started", "a")
    w.feed("item_finished", "a", verdict="done")
    w.feed("check_verdict", "a", result="green", review="no: still")
    assert w.label("a") == "proposed" and w.it("a").review == "no: still"
    assert "the review says no again" in w.it("a").why
    w.feed("seen", "a", queueable=True)                # objected twice: a person's call
    assert w.label("a") == "proposed"


def test_green_and_ok_proposes():
    w = building(EKI)
    w.feed("item_finished", "a", verdict="done")
    w.feed("check_verdict", "a", result="green", review="ok", branch="self/a")
    assert w.label("a") == "proposed" and w.it("a").why == "reviewed ok — proposed on self/a"


def test_a_build_that_ended_early_goes_to_the_fixer():
    w = building(EKI)
    acts = w.feed("item_finished", "a", verdict="done", changed=False, ended_early=True)
    assert w.label("a") == "fixing" and acts[0].data["stage"] == "nothing"
    w = building(PROJECT)
    w.feed("item_finished", "a", verdict="done", changed=False, ended_early=True)
    assert w.label("a") == "left"


def test_nothing_changed_is_left_and_a_person_verdict_pushes():
    w = building(EKI)
    assert w.feed("item_finished", "a", verdict="done", changed=False) == ()
    assert w.label("a") == "left" and w.it("a").why == "left for you — the agent changed nothing"
    w = building(EKI)
    acts = w.feed("item_finished", "a", verdict="person", reason="which API?")
    assert w.label("a") == "left" and kinds(acts) == [("notify", None)]
    assert acts[0].data["key"] == "item:a:left:1"


def test_waiting_on_calls_then_woken_on_the_same_try():
    w = building(EKI)
    w.feed("item_finished", "a", calls=2, wakes=0, cards="music (r1)")
    assert w.label("a") == "waiting on calls" and w.it("a").why == "waiting on calls — music (r1)"
    acts = w.feed("item_started", "a", stage="building")
    assert w.label("a") == "building" and w.it("a").tries == 1
    assert acts[0].data == {"role": "build", "stage": "building", "tries": 1, "again": True}
    w.feed("item_finished", "a", calls=2, wakes=4, verdict="done")     # past the bound: concluded
    assert w.label("a") == "judging"


def test_locked_files_hold_an_eki_item_for_a_person():
    w = World(EKI, [item("a", "proposed", touched=("bin/check", "x.py"))])
    acts = w.feed("seen", "a", queueable=True)
    assert w.label("a") == "locked" and w.it("a").why == "locked — touches bin/check; needs a person"
    assert kinds(acts) == [("notify", None)]
    with pytest.raises(workstep.Refused, match="touches locked files: bin/check, needs --yes"):
        w.feed("person_decided", "a", decision="apply")
    assert kinds(w.feed("person_decided", "a", decision="apply", yes=True)) == [("merge", None)]
    assert w.label("a") == "queued"
    p = World(PROJECT, [item("a", "proposed", touched=("bin/check",))])
    p.feed("seen", "a", queueable=True)
    assert p.label("a") == "queued"
    assert workstep_queue.locked_in(("eki/*.py",), ("eki/drill.py", "bin/check")) == ["eki/drill.py"]


@pytest.mark.parametrize("settings", [EKI, PROJECT])
def test_admit_only_under_merge_auto(settings):
    from dataclasses import replace
    w = World(replace(settings, merge="person"), [item("a", "proposed")])
    assert w.feed("seen", "a", queueable=True) == () and w.label("a") == "proposed"
    w = World(settings, [item("a", "proposed")])
    assert w.feed("seen", "a", queueable=False) == () and w.label("a") == "proposed"
    assert kinds(w.feed("seen", "a", queueable=True)) == [("merge", None)]
    assert w.label("a") == "queued" and w.it("a").phase == "merging"


def stack(*results, ids="abcd"):
    return [{"id": i, "result": r} for i, r in zip(ids, results)]


def test_batch_bisect_finds_the_bad_one_and_the_good_ones_land():
    w = World(EKI, [item(i, "queued") for i in "abcd"])
    acts = w.feed("merge_result", "a", result="rebased", on="m0", stack=stack(None, None, None, None))
    gate = [a for a in acts if a.kind == "run_gate"]
    assert [(a.item, a.data) for a in gate] == [("d", {"gate": 2, "batch": ["a", "b", "c", "d"]})]
    assert gate[0].why == "gate 2 on a batch of 4 (a..d)"
    assert {w.label(i) for i in "abcd"} == {"queued"} and w.it("a").stage == "gate 2"
    acts = w.feed("check_verdict", None, gate=2, stack=stack(None, None, None, "red"))
    assert [(a.item, a.data["batch"]) for a in acts] == [("b", ["a", "b"])]
    acts = w.feed("check_verdict", None, gate=2, stack=stack(None, "green", None, "red"))
    assert w.it("a").gate2 == "green" and w.it("a").why == "gate 2 green (in the batch checked on b)"
    assert [(a.kind, a.item) for a in acts] == [("land", "a"), ("land", "b"), ("run_gate", "c")]
    w.feed("check_verdict", None, gate=2, stack=stack(None, "green", "green", "red"))
    assert w.it("d").why == "gate 2 red; waits for the green items under it to land" and w.label("d") == "queued"
    for i in "abc":
        w.feed("landed", i, sha="s" * 40)
        assert w.label(i) == "landed"
    acts = w.feed("check_verdict", None, gate=2, stack=stack("red", ids="d"), tails={"d": "E broke"})
    assert w.label("d") == "fixing" and acts[0].data["failure"] == "E broke"


def test_bisect_by_itself():
    assert workstep_queue.bisect([None, None]) == (-1, "start", 0, 1)
    assert workstep_queue.bisect([None, "running"]) == (-1, None, 0, 0)
    assert workstep_queue.bisect(["green", "green"]) == (1, None, 2, 2)
    assert workstep_queue.bisect([None, None, None, None, "red"]) == (-1, "start", 0, 1)
    assert workstep_queue.bisect(["red"]) == (-1, "bad", 0, 0)
    assert workstep_queue.bisect(["green", "red"]) == (0, "waits", 1, 1)


@pytest.mark.parametrize("settings", [EKI, PROJECT])
def test_a_red_front_waits_for_the_greens_under_it_on_eki_and_on_a_project(settings):
    """Fixed bug (c): a project used to make it unfit at once, its fixer on the old tip."""
    w = World(settings, [item("a", "queued"), item("b", "queued")])
    w.feed("check_verdict", None, gate=2, stack=stack("green", "red"))
    assert w.label("b") == "queued" and "waits for the green items under it" in w.it("b").why
    w.feed("landed", "a", sha="1" * 40, branch="main")
    assert w.label("a") == ("landed" if settings.kind == "eki" else "applied")
    w.feed("check_verdict", None, gate=2, stack=stack("red", ids="b"))
    assert w.label("b") == ("fixing" if settings.kind == "eki" else "unfit")


def test_a_project_without_a_check_is_not_judged():
    s = Settings(gates=(), merge="auto", parallel=0, kind="local")
    w = building(s)
    assert w.feed("item_finished", "a", verdict="done", branch="eki/a") == ()
    assert w.label("a") == "proposed"
    assert w.it("a").why == "built — proposed on eki/a (no check configured — not judged)"
    w.feed("seen", "a", queueable=True)
    acts = w.feed("merge_result", "a", result="rebased", unbatched="gate 2 not judged — p has no check")
    assert w.it("a").gate2 == "green" and w.it("a").why == "gate 2 not judged — p has no check"
    assert kinds(acts) == [("land", None)]


def test_a_card_without_batch_is_not_batched():
    s = Settings(gates=("check", "review"), merge="auto", parallel=2, kind="local")
    w = World(s, [item("a", "queued"), item("b", "queued")])
    w.feed("merge_result", "a", result="rebased", unbatched="gate 2 skipped — not batched: checks: self, review")
    assert w.it("a").gate2 == "green" and "not batched" in w.it("a").why
    assert w.feed("check_verdict", None, gate=2, stack=stack(None, None)) == ()


def test_already_in_the_target_is_landed_or_applied():
    w = World(EKI, [item("p", "proposed"), item("l", "locked"), item("q", "queued")])
    w.feed("seen", "p", in_target=True, where="abc is in main")
    w.feed("seen", "l", in_target=True)
    w.feed("seen", "q", in_target=True, sha="f" * 40)
    assert [w.label(i) for i in "plq"] == ["applied", "landed", "landed"]
    assert w.it("p").why == "applied — abc is in main"
    w.feed("landed", "q", went="live", why="live in build b1")
    assert w.label("q") == "live" and w.it("q").phase == "done"
    p = World(PROJECT, [item("q", "queued")])
    p.feed("seen", "q", in_target=True, sha="f" * 40, branch="main")
    assert p.label("q") == "applied" and p.it("q").why == f"landed on main ({'f' * 12})"


def test_a_land_that_waits_says_why_once():
    w = World(PROJECT, [item("q", "queued", gate2="green")])
    w.feed("landed", "q", waits="p has local changes in x.py")
    assert w.label("q") == "queued" and w.it("q").why == "waits: p has local changes in x.py"


def test_quota_holds_until_retry_at_and_the_try_is_not_counted():
    w = building(EKI)
    acts = w.feed("item_failed", "a", kind="quota", retry_at=500.0, fix=None)
    assert w.label("a") == "waiting" and w.it("a").tries == 0
    assert [(a.kind, a.data) for a in acts] == [("hold_until", {"until": 500.0})]
    assert w.feed("item_started", "a", at=100.0) == () and w.label("a") == "waiting"
    w.feed("item_started", "a", at=600.0)
    assert w.label("a") == "building" and w.it("a").tries == 1


def test_environment_asks_a_person_without_spending_the_fixer():
    w = building(EKI)
    acts = w.feed("item_failed", "a", kind="environment", why="no disk", fix=None)
    assert w.label("a") == "left" and not w.it("a").fixer_used and kinds(acts) == [("ask_person", None)]


def test_a_lost_run_starts_again_and_is_not_a_try():
    w = building(EKI)
    acts = w.feed("run_lost", "a")
    assert w.label("a") == "building" and w.it("a").tries == 1
    assert acts[0].data == {"role": "build", "stage": "building", "tries": 1, "again": True}
    w.feed("item_failed", "a", kind="lost")
    assert w.it("a").tries == 1


def test_a_person_retries_drops_and_applies():
    w = World(EKI, [item("u", "unfit", tries=2, fixer_used=True), item("b", "building"), item("p", "proposed"),
                    item("w")])
    w.feed("person_decided", "u", decision="retry")
    assert w.label("u") == "waiting" and not w.it("u").fixer_used and w.it("u").tries == 2
    with pytest.raises(workstep.Refused, match="item b is building"):
        w.feed("person_decided", "b", decision="retry")
    w.feed("person_decided", "b", decision="drop")
    assert w.label("b") == "dropped"
    assert kinds(w.feed("person_decided", "p", decision="apply")) == [("merge", None)]
    with pytest.raises(workstep.Refused, match="item w is waiting, not proposed"):
        w.feed("person_decided", "w", decision="apply")
    g = World(EKI, stage="failed")
    assert kinds(g.feed("person_decided", None, decision="retry")) == [("start_run", "plan")]


def test_a_conflict_resolves_and_comes_back_at_the_back():
    w = World(EKI, [item("a", "queued")])
    acts = w.feed("merge_result", "a", result="conflict", files=["x.py"])
    assert w.label("a") == "resolving" and acts[0].data["role"] == "resolve"
    w.feed("item_finished", "a", outcome="bad", why="no ITEM line")
    assert w.label("a") == "resolving"
    w.feed("item_finished", "a", outcome="bad", why="no ITEM line", retried=True)
    assert w.label("a") == "fixing"                    # left, but its one fixer run first
