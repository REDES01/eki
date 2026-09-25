# SPDX-License-Identifier: Apache-2.0
"""A restart at any moment loses nothing (eki/steps.py): every step of
self-work is written down before it starts, one cut off is taken up again
once — never reported as failed — the roadmap is ticked by one writer, and
applied changes go live together on the release train."""
import json
import sys
from pathlib import Path

import pytest

from eki import builds, candidate, roadmap, selfloop, selfwork, steps
from eki.adapters.base import BackendError
from eki.runs import Runner, RunStore
from tests.test_runs import settle
from tests.test_selfloop import PLAN, Agent, _conflicting, eng, git, turn      # noqa: F401  (the fixture)


# ---- the ledger ----------------------------------------------------------------------------------

def test_a_step_cut_off_by_a_restart_is_interrupted_and_taken_up_once(monkeypatch):
    steps.start("check", "i1", item="i1", run="r1")
    steps.start("swap", "b1", target="/x")
    assert steps.live(steps.get("check", "i1"), ["r1"])
    monkeypatch.setattr(steps, "BOOT", "the-next-engine")                  # the restart
    assert not steps.live(steps.get("check", "i1"), ["r1"])                # no spinner for it
    cut = steps.recover()
    assert [s["subject"] for s in cut] == ["i1"]                            # a swap runs outside
    assert steps.get("check", "i1")["state"] == "interrupted"
    assert steps.get("swap", "b1")["state"] == "running"
    assert steps.waiting(steps.get("check", "i1"))
    assert steps.claim("check", "i1") and not steps.claim("check", "i1")    # never twice
    assert steps.get("check", "i1")["tries"] == 1
    steps.end("check", "i1", "succeeded")
    assert not steps.unfinished() or [s["kind"] for s in steps.unfinished()] == ["swap"]


def test_a_step_cut_off_again_and_again_is_failed_so_a_crash_cant_loop(monkeypatch):
    steps.start("agent", "i2", item="i2")
    for n in range(steps.MAX_TRIES):
        monkeypatch.setattr(steps, "BOOT", f"engine-{n}")
        steps.recover()
        if not steps.claim("agent", "i2"):
            break
    assert steps.get("agent", "i2")["state"] == "failed"
    assert "cut off" in steps.get("agent", "i2")["note"]


def test_a_step_this_engine_saw_cut_off_waits_a_moment_first():
    steps.start("resolve", "c1", run="gone")
    steps.end("resolve", "c1", "interrupted", "exit 143")
    s = steps.get("resolve", "c1")
    assert not steps.waiting(s)                          # an engine on its way out leaves it
    assert steps.waiting(s, now=float(s["at"]) + steps.LOST_GRACE)


def test_what_counts_as_cut_off():
    for said in ("claude exited with code 143", "Command failed with exit code 137",
                 "killed by signal", "SIGTERM", -15, 143, steps.Interrupted("x")):
        assert steps.cut_off(said), said
    for said in ("exit code 1", "rate limited", "143 tests passed", 1, 0, None):
        assert not steps.cut_off(said), said


def test_tests_cut_off_by_a_signal_are_not_a_failing_check(tmp_path):
    root = tmp_path / "co"
    (root / "tests").mkdir(parents=True)
    (root / "eki").mkdir()
    (root / "eki" / "cli.py").write_text("")
    (root / "tests" / "test_cut.py").write_text(
        "import os, signal\n\ndef test_cut():\n    os.kill(os.getpid(), signal.SIGTERM)\n")
    with pytest.raises(steps.Interrupted):
        candidate.check_tests(root, sys.executable)
    with pytest.raises(steps.Interrupted):                # no report at all — not "tests ✗"
        candidate.check(root, python=sys.executable, skip=("app", "data"))
    (root / "tests" / "test_cut.py").write_text("def test_no():\n    assert False\n")
    with pytest.raises(RuntimeError) as failed:           # a real failure is still a verdict
        candidate.check_tests(root, sys.executable)
    assert not isinstance(failed.value, steps.Interrupted)


@pytest.mark.asyncio
async def test_a_run_cut_off_is_interrupted_not_failed(tmp_path):
    store = RunStore(tmp_path / "runs.db", owner=True)

    async def dispatch(run):
        yield "half an answer"
        raise steps.Interrupted("the tests were cut off (exit -15)")

    runner = Runner(store, dispatch)
    rid = store.create("judge it")
    await runner.submit(rid)
    run = await settle(store, rid)
    assert run["state"] == "interrupted" and "cut off" in run["error"]
    assert json.loads(run["payload"])["handed_over"] == 1


# ---- in the engine ---------------------------------------------------------------------------------

def _restart(monkeypatch, eng):
    """What the next engine does first: the steps the last one left running
    are cut off (the engine object stays — only its identity changes)."""
    monkeypatch.setattr(steps, "BOOT", f"after-{steps.BOOT}")
    eng.note_interruptions()


@pytest.mark.asyncio
async def test_an_interrupted_candidate_check_is_run_again_not_failed(eng, monkeypatch):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    judged = []

    def check(where, **kw):
        judged.append(git(where, "rev-parse", "HEAD"))
        if len(judged) == 1:
            raise steps.Interrupted("the tests were cut off (exit -15)")      # the swap took pytest along
        return candidate.Report(str(where), [candidate.Check("tests", True, "stand-in")])

    eng.self_check = check
    started = await eng.self_ask("make VALUE two")
    first = await settle(eng.runs, started["run"], timeout=20)
    assert first["state"] == "interrupted"                                 # not failed
    it = selfloop.get(started["item"])
    assert it.state == "working" and it.phase == "checking"
    assert steps.get("check", it.id)["state"] == "interrupted"
    assert not [c for c in selfwork.changes() if c["state"] == "unfit"]    # no "tests ✗"
    _restart(monkeypatch, eng)
    assert await eng.self_carry_on() == 1
    assert await eng.self_carry_on() == 0                                  # once
    again = selfloop.get(started["item"]).run
    await settle(eng.runs, again, timeout=20)
    c = selfwork.change(selfloop.get(started["item"]).change)
    assert c["state"] == "proposed" and c["fit"] and c["files"] == ["eki/thing.py"]
    assert judged[0] == judged[1] == c["commit"]                           # the same commit, judged again
    assert len(Agent.seen) == 1                                            # the agent wasn't asked twice
    assert steps.get("check", it.id)["state"] == "succeeded"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_an_interrupted_resolve_is_carried_on_and_finishes(eng, monkeypatch):
    cid = await _conflicting(eng)
    real, cut = Agent.stream, {"left": 1}

    async def stream(self, messages, **kw):
        if "resolving conflicts" in messages[-1].content and cut["left"]:
            cut["left"] -= 1
            raise BackendError("claude exited with code 143")               # SIGTERM from the restart
        async for piece in real(self, messages, **kw):
            yield piece

    monkeypatch.setattr(Agent, "stream", stream)
    Agent.edits = {"README.md": "eki, your way — and the agent's\n"}
    Agent.answers = ["README.md: kept your wording and added the agent's."]
    got = await eng.self_apply(cid)
    first = await settle(eng.runs, got["resolving"], timeout=20)
    assert first["state"] == "interrupted"
    c = selfwork.change(cid)
    assert c["state"] == "conflicts" and c["resolving"] == got["resolving"]   # not given up
    assert "couldn't resolve" not in (c.get("why") or "")
    assert selfwork.rebasing(Path(c["worktree"]))                             # left mid-rebase
    row = next(r for r in eng.self_view()["changes"] if r["id"] == cid)
    assert row["resolving"] and not row["resolving_live"]                     # no spinner for it
    _restart(monkeypatch, eng)
    assert await eng.self_carry_on() == 1
    again = selfwork.change(cid)["resolving"]
    assert again != got["resolving"]
    await settle(eng.runs, again, timeout=20)
    told = Agent.seen[-1][1]
    assert "Carry on where you left off" in told and "README.md" in told
    c = selfwork.change(cid)
    assert c["state"] == "applied" and not c.get("resolving")
    assert (eng.root / "README.md").read_text() == "eki, your way — and the agent's\n"
    assert steps.get("resolve", cid)["state"] == "succeeded"
    said = eng.store.turns(got["conversation"])[-1]["content"]
    assert "Conflicts in `README.md` were resolved" in said
    assert await eng.self_carry_on() == 0
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_ticks_from_two_changes_landing_back_to_back_dont_conflict(eng):
    """Two roadmap items, both made from the same checkout, each agent
    ticking its own item (it was told not to). The ticks aren't in the
    changes; the merge queue writes each once its change lands."""
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_parallel": 1}
    items = roadmap.workable(roadmap.parse(PLAN))
    for n, item in enumerate(items[:2]):
        Agent.edits = {f"docs/{n}.md": f"part {n}\n", "ROADMAP.md": roadmap.tick(PLAN, item.key, "*(mine)*")}
        Agent.answers = ["Done.\nITEM: done"]
        await turn(eng)
    made = [selfwork.change(i.change) for i in selfloop.items() if i.source == "roadmap"]
    assert len(made) == 2 and all(c["state"] == "proposed" for c in made)
    assert [c["files"] for c in made] == [["docs/0.md"], ["docs/1.md"]]      # no ROADMAP.md in either
    for c in made:
        got = await eng.self_apply(c["id"])
        assert got["state"] == "applied" and not got.get("resolving")
    text = (eng.root / "ROADMAP.md").read_text()
    for c, item in zip(made, items):
        assert roadmap.find(text, item.key).done and roadmap.mark(c["id"]) in text
    assert "*(mine)*" not in text
    log = git(eng.root, "log", "--format=%s").splitlines()
    assert sum(s.startswith("roadmap: tick") for s in log) == 2
    # undone, the tick goes too — as its own commit
    undo = await eng.self_undo(made[0]["id"])
    assert undo["state"] == "applied"
    text = (eng.root / "ROADMAP.md").read_text()
    assert not roadmap.find(text, items[0].key).done and roadmap.find(text, items[1].key).done
    assert git(eng.root, "log", "-1", "--format=%s").startswith("roadmap: untick")
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_applies_within_a_window_go_live_together(eng):
    async def made(request, edits):
        Agent.edits = edits
        started = await eng.self_ask(request)
        await settle(eng.runs, started["run"], timeout=20)
        return selfloop.get(started["item"]).change

    a = await made("make VALUE two", {"eki/thing.py": "VALUE = 2\n"})
    b = await made("add another", {"eki/other.py": "OTHER = 1\n"})
    c = await made("add a third", {"eki/third.py": "THIRD = 1\n"})
    assert (await eng.self_apply(a))["state"] == "applying"
    assert [kw["self_id"] for _, kw in eng.swaps] == [a]                   # the first goes at once
    assert (await eng.self_apply(b))["state"] == "applying"
    assert (await eng.self_apply(c))["state"] == "applying"
    assert len(eng.swaps) == 1                                             # the rest wait for the window
    train = eng.self_view()["train"]
    assert [x["id"] for x in train["carrying"]] == [b, c] and 0 < train["in"] <= 15 * 60
    assert await eng.self_release() == {}                                  # not yet
    t = builds.train()
    builds._save_train({**t, "last": t["last"] - 15 * 60})                 # the window has passed
    gone = await eng.self_release()
    assert gone["cars"] == [b, c] and len(eng.swaps) == 2
    build, kw = eng.swaps[1]
    assert kw == {"self_id": c}
    carried = json.loads((build / builds.MARK).read_text())["commit"]
    assert selfwork.is_in(eng.root, selfwork.change(b)["commit"], carried)  # c's build carries b
    assert eng.self_view()["train"] == {}
    assert steps.get("swap", build.name)["state"] == "running"
    eng.self_settled({"self": c, "state": "healthy", "target": str(build), "cars": [b, c]})
    assert selfwork.change(b)["state"] == selfwork.change(c)["state"] == "applied"
    assert steps.get("swap", build.name)["state"] == "succeeded"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_your_own_apply_can_go_live_now(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    first = await eng.self_ask("make VALUE two")
    await settle(eng.runs, first["run"], timeout=20)
    Agent.edits = {"eki/other.py": "OTHER = 1\n"}
    second = await eng.self_ask("add another")
    await settle(eng.runs, second["run"], timeout=20)
    await eng.self_apply(selfloop.get(first["item"]).change)
    await eng.self_apply(selfloop.get(second["item"]).change, now=True)
    assert len(eng.swaps) == 2                                             # it didn't wait its window
    await eng.runner.stop()


def test_the_command_line_says_when_the_next_go_live_is():
    from eki.cli import common as cli
    line = cli._next_go_live({"in": 400, "every": 15, "carrying": [{"id": "ab12", "title": "make it so"}]})
    assert line.startswith("next go-live in 7 min, carrying: self/ab12 (make it so)")
    assert cli._next_go_live({}) == ""
    many = {"in": 0, "carrying": [{"id": f"c{n}", "title": "x" * 50} for n in range(8)]}
    assert "and 2 more (eki self --all)" in cli._next_go_live(many) and "x" * 50 not in cli._next_go_live(many)
    whole = cli._next_go_live(many, everything=True)
    assert "self/c7" in whole and "x" * 50 in whole and "more" not in whole


def test_eki_self_all_shows_what_is_cut_off(monkeypatch, capsys):
    from eki.cli import self_ as cli
    long = "a title long enough that a terminal line would cut it off somewhere " * 2
    view = {"can": True, "goal": None, "autonomy": "propose", "review_max": 3, "areas": {}, "tiers": {},
            "working": [], "merging": [], "waiting": [], "queue": [], "left": [],
            "roadmap": {"next": [{"title": f"item {n}", "section": "Stage 1 — x"} for n in range(7)]},
            "changes": [{"id": f"c{n}", "state": "applied", "state_at": 0, "title": long} for n in range(25)]}
    monkeypatch.setattr(cli, "call", lambda *a, **kw: view)
    cli._self_status("s", 20)
    cut = capsys.readouterr().out
    assert "item 2" in cut and "item 3" not in cut and "… 4 more on the roadmap — eki self --all shows them" in cut
    assert "self/c19" in cut and "self/c20" not in cut and "… 5 more older" in cut and long.strip() not in cut
    cli._self_status("s", 20, everything=True)
    whole = capsys.readouterr().out
    assert "item 6" in whole and "self/c24" in whole and long.strip() in whole and "more" not in whole
