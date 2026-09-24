# SPDX-License-Identifier: Apache-2.0
"""A run the engine lost to a restart (a crash, a swap) carries on by itself
where the program kept its session — once — in the copy it was working in."""
import json
import subprocess
from pathlib import Path

import pytest

from eki import workspace
from eki.engine import Engine
from tests.test_runs import echo_config, settle
from tests.test_workspace import repo, sh      # noqa: F401  (the fixture)


def engine(tmp_path, **settings):
    eng = Engine(echo_config(tmp_path), owner=True)
    eng.settings = {**eng.settings, "skills_learn": "off", "resume_interrupted": True, **settings}
    return eng


def lost_run(eng, *, session=True, payload="", cwd=""):
    """A run the previous engine was in the middle of."""
    cid = eng.store.new_conversation("t")
    turn = eng.store.add_turn(cid, "user", "do the long thing")
    rid = eng.runs.create("do the long thing", conversation=cid, requested="echo",
                          user_turn=turn, payload=payload, cwd=cwd)
    eng.runs.update(rid, state="running", backend="echo", started_at=1)
    if session:
        eng.store.set_session(cid, "echo", "sess-1")
    return cid, rid


@pytest.mark.asyncio
async def test_a_run_with_a_session_carries_on_by_itself(tmp_path):
    first = engine(tmp_path)
    cid, rid = lost_run(first)
    await first.runner.stop()
    second = engine(tmp_path)                       # the restart
    assert second.note_interruptions() == 1
    assert "carrying on" in second.store.turns(cid)[-1]["content"]
    assert await second.resume_interrupted() == 1
    new = [r for r in second.runs.recent() if r["conversation_id"] == cid and r["id"] != rid][0]
    run = await settle(second.runs, new["id"])
    assert run["state"] == "done" and json.loads(run["payload"])["resume_of"] == rid
    assert second.store.turns(cid)[-2]["content"] == "Carry on where you left off."
    await second.runner.stop()


@pytest.mark.asyncio
async def test_a_folder_run_without_a_session_waits_for_you(tmp_path):
    first = engine(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    cid, _ = lost_run(first, session=False, cwd=str(folder))
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert second.store.turns(cid)[-1]["content"] == "*[interrupted — the engine restarted]*"
    assert await second.resume_interrupted() == 0       # it may have made half its edits
    await second.runner.stop()


@pytest.mark.asyncio
async def test_an_answer_cut_off_with_no_folder_or_session_is_asked_again(tmp_path):
    # the restart drill (eki/drill.py): a model's answer cut off mid-stream
    # used to wait for you — asking again repeats nothing but words
    first = engine(tmp_path)
    cid, rid = lost_run(first, session=False)
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert "carrying on" in second.store.turns(cid)[-1]["content"]
    assert await second.resume_interrupted() == 1
    new = [r for r in second.runs.recent() if r["conversation_id"] == cid and r["id"] != rid][0]
    run = await settle(second.runs, new["id"])
    assert run["state"] == "done" and json.loads(run["payload"])["retry_of"] == rid
    assert run["user_turn"] == first.runs.get(rid)["user_turn"]   # the question isn't asked twice
    await second.runner.stop()


@pytest.mark.asyncio
async def test_a_resumption_that_is_itself_lost_is_not_resumed_again(tmp_path):
    first = engine(tmp_path)
    lost_run(first, payload=json.dumps({"resume_of": "older"}))
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert await second.resume_interrupted() == 0       # no crash loop
    await second.runner.stop()


def handed_over(eng, rid):
    """What `Runner.stop` does to a run it cuts off: the engine went away on purpose."""
    eng.runs.hand_over(rid)


@pytest.mark.asyncio
async def test_a_resumption_cut_off_by_a_swap_carries_on_again(tmp_path):
    first = engine(tmp_path)
    cid, rid = lost_run(first, payload=json.dumps({"resume_of": "older", "carries": 1}))
    handed_over(first, rid)
    await first.runner.stop()
    second = engine(tmp_path)                       # the swap
    second.note_interruptions()
    assert await second.resume_interrupted() == 1
    new = [r for r in second.runs.recent() if r["conversation_id"] == cid and r["id"] != rid][0]
    run = await settle(second.runs, new["id"])
    assert json.loads(run["payload"])["resume_of"] == rid and json.loads(run["payload"])["carries"] == 2
    await second.runner.stop()


@pytest.mark.asyncio
async def test_carrying_on_after_swaps_has_a_bound(tmp_path):
    from eki.engine import MAX_CARRIES
    first = engine(tmp_path)
    _, rid = lost_run(first, payload=json.dumps({"resume_of": "older", "carries": MAX_CARRIES}))
    handed_over(first, rid)
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert await second.resume_interrupted() == 0
    await second.runner.stop()


@pytest.mark.asyncio
async def test_a_run_that_never_began_is_asked_again(tmp_path):
    first = engine(tmp_path)
    cid = first.store.new_conversation("t")
    turn = first.store.add_turn(cid, "user", "hello")
    rid = first.runs.create("hello", conversation=cid, requested="echo", user_turn=turn)
    await first.runner.stop()                       # queued, never started
    second = engine(tmp_path)
    second.note_interruptions()
    assert await second.resume_interrupted() == 1
    new = [r for r in second.runs.recent() if r["conversation_id"] == cid and r["id"] != rid][0]
    run = await settle(second.runs, new["id"])
    assert run["state"] == "done" and json.loads(run["payload"])["retry_of"] == rid
    await second.runner.stop()


@pytest.mark.asyncio
async def test_a_run_is_never_carried_on_twice(tmp_path):
    first = engine(tmp_path)
    cid, rid = lost_run(first)
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert await second.resume_interrupted() == 1
    assert await second.resume(rid) is None          # by hand, after it carried on
    assert await second.retry(rid) is None
    assert len([r for r in second.runs.recent() if r["conversation_id"] == cid]) == 2
    await second.runner.stop()


# ---- self-work: its item follows, and is taken up once ------------------------------

def self_run(eng, *, session, handed=True):
    from eki import selfloop
    it = selfloop.add("asked", "make it better", "make it better", when="now")
    cid, rid = lost_run(eng, session=session, payload=json.dumps({"self_item": it.id}))
    selfloop.update(it.id, state="working", run=rid, conversation=cid)
    if handed:
        handed_over(eng, rid)
    return it, cid, rid


@pytest.mark.asyncio
async def test_self_work_with_a_session_carries_on_and_its_item_follows_at_once(tmp_path, monkeypatch):
    from eki import selfloop
    first = engine(tmp_path)
    it, cid, rid = self_run(first, session=True)
    await first.runner.stop()
    second = engine(tmp_path)
    started = []
    real = second.runner.submit
    monkeypatch.setattr(second.runner, "submit", lambda r: started.append(r) or real(r))

    async def no_self_work(run):                    # the pipeline itself is tested elsewhere
        yield "carrying on"
    monkeypatch.setattr(second, "_self_work", no_self_work)
    second.note_interruptions()
    assert await second.resume_interrupted() == 1
    new = second.runs.get(started[0])
    assert json.loads(new["payload"])["self_item"] == it.id and json.loads(new["payload"])["resume_of"] == rid
    # the loop, looking now, sees the item being worked on — not something to take up
    assert selfloop.get(it.id).run == new["id"]
    assert await second._self_take_up(rid) is None and await second.resume(rid) is None
    await second.runner.stop()


@pytest.mark.asyncio
async def test_self_work_cut_off_before_a_session_is_taken_up_again_once(tmp_path, monkeypatch):
    first = engine(tmp_path)
    it, cid, rid = self_run(first, session=False)
    await first.runner.stop()
    second = engine(tmp_path)
    taken = []

    async def self_start(item, *a, **k):
        taken.append(item.id)
        from eki import selfloop
        selfloop.update(item.id, run="new-run")
        return {"run": "new-run", "conversation": cid}
    monkeypatch.setattr(second, "_self_start", self_start)
    second.note_interruptions()
    assert "carrying on" in second.store.turns(cid)[-1]["content"]
    assert await second.resume_interrupted() == 1 and taken == [it.id]
    assert await second._self_take_up(rid) is None and taken == [it.id]     # never twice
    await second.runner.stop()


@pytest.mark.asyncio
async def test_self_work_lost_to_a_crash_without_a_session_is_left_to_the_loop(tmp_path):
    first = engine(tmp_path)
    self_run(first, session=False, handed=False)
    await first.runner.stop()
    second = engine(tmp_path)
    second.note_interruptions()
    assert await second.resume_interrupted() == 0
    await second.runner.stop()


@pytest.mark.asyncio
async def test_off_means_you_decide(tmp_path):
    first = engine(tmp_path)
    lost_run(first)
    await first.runner.stop()
    second = engine(tmp_path, resume_interrupted=False)
    second.note_interruptions()
    assert await second.resume_interrupted() == 0
    await second.runner.stop()


def test_carrying_on_takes_the_copy_as_the_run_left_it(repo):
    ws = workspace.open(str(repo), "c1")
    (Path(ws.path) / "half.py").write_text("half done\n")          # then the engine died
    (repo / "README.md").write_text("you, meanwhile\n")
    again = workspace.open(str(repo), "c1", keep=True)
    assert again.start == ws.start and (Path(again.path) / "half.py").exists()
    assert (Path(again.path) / "README.md").read_text() == "hello\n"   # not synced over
    got = workspace.close(again, "r2")
    assert got["state"] == "applied" and (repo / "half.py").exists()
    assert (repo / "README.md").read_text() == "you, meanwhile\n"
    fresh = workspace.open(str(repo), "c1", keep=True)                # closed: a normal sync
    assert (Path(fresh.path) / "README.md").read_text() == "you, meanwhile\n"


# ---- a restart is not you pressing stop ------------------------------------------

from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register  # noqa: E402


@register("sloth")
class Sloth(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield "working…"
        import asyncio
        await asyncio.sleep(30)
        yield "done"


@pytest.mark.asyncio
async def test_a_run_cut_off_by_a_restart_is_carried_on_not_cancelled(tmp_path, repo):
    import asyncio
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(key="sloth", kind="sloth", label="sloth",
                                    capabilities=Capabilities(repo=True, tools=True), cost=Cost(tier=0)))
    cfg.options["sloth"] = {}
    first = Engine(cfg, owner=True)
    first.settings = {**first.settings, "skills_learn": "off", "resume_interrupted": True}
    started = await first.ask("a long job", repo=str(repo), backend_key="sloth")
    cid, rid = started["conversation"], started["run"]
    for _ in range(100):
        if (first.runs.get(rid) or {}).get("output"):
            break
        await asyncio.sleep(0.02)
    first.store.set_session(cid, "sloth", "sess-9")
    copy = Path(json.loads(json.dumps(first._in_copy and list(first._in_copy)[0])))
    (copy / "partial.txt").write_text("made before the restart\n")
    await first.runner.stop()                           # the engine going away
    assert first.runs.get(rid)["state"] == "running"    # not "cancelled"
    assert json.loads(first.runs.get(rid)["payload"])["handed_over"] == 1   # on purpose, not a crash
    assert not any("[stopped]" in t["content"] for t in first.store.turns(cid))
    assert not (repo / "partial.txt").exists()          # nothing forced back, nothing kept yet

    second = Engine(cfg, owner=True)
    second.settings = {**second.settings, "skills_learn": "off", "resume_interrupted": True}
    assert second.runs.get(rid)["state"] == "interrupted"
    second.note_interruptions()
    assert await second.resume_interrupted() == 1
    new = [r for r in second.runs.recent() if r["conversation_id"] == cid and r["id"] != rid][0]
    for _ in range(100):                                 # it's working in the same copy
        if second._in_copy:
            break
        await asyncio.sleep(0.02)
    assert str(copy) in second._in_copy and (copy / "partial.txt").exists()
    await second.runner.stop()
