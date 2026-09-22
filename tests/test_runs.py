# SPDX-License-Identifier: Apache-2.0
"""Runs: the one primitive, and the ways it can quietly go wrong.

Most of these exist because of a specific failure:
  * reopening the run database from a second process marked the engine's
    live work as interrupted while it was still running;
  * a watcher joining mid-run lost every chunk that landed between reading
    the log and subscribing to it;
  * two parallel runs on one adapter instance would hand each other their
    session ids;
  * a failure's reason was published after the terminal state, which is the
    point at which every watcher stops reading.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eki.adapters.base import (Backend, BackendInfo, Capabilities, Cost, Health,
                               register)
from eki.config import Config
from eki.engine import Engine
from eki.runs import Runner, RunStore, unseen


@pytest.fixture(autouse=True)
def private_policy(tmp_path, monkeypatch):
    # the engine reads ~/.eki/policy.json; a test must not inherit yours
    monkeypatch.setenv("EKI_POLICY", str(tmp_path / "policy.json"))


@pytest.fixture
def store(tmp_path):
    s = RunStore(tmp_path / "runs.db", owner=True)
    yield s
    s.close()


async def settle(store, rid, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get(rid)["state"] in ("done", "failed", "cancelled", "interrupted"):
            return store.get(rid)
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {rid} never settled: {store.get(rid)['state']}")


# ---- the store ------------------------------------------------------------

def test_create_starts_queued(store):
    rid = store.create("do a thing", conversation="c1", cwd="/tmp", requested="codex")
    run = store.get(rid)
    assert run["state"] == "queued"
    assert run["conversation_id"] == "c1"
    assert run["output"] == ""


def test_recent_is_newest_first_even_within_a_second(store):
    a = store.create("first")
    b = store.create("second")
    assert [r["id"] for r in store.recent()] == [b, a]


def test_active_finds_the_run_a_conversation_is_waiting_on(store):
    store.create("old", conversation="c")
    done = store.create("older", conversation="c")
    store.update(done, state="done")
    rid = store.create("now", conversation="c")
    store.update(rid, state="running")
    assert store.active("c")["id"] == rid
    assert store.active("elsewhere") is None


def test_the_engine_reopening_marks_orphans_interrupted(tmp_path):
    first = RunStore(tmp_path / "runs.db", owner=True)
    rid = first.create("long one")
    first.update(rid, state="running")
    first.close()
    second = RunStore(tmp_path / "runs.db", owner=True)
    assert second.get(rid)["state"] == "interrupted"


def test_a_reader_opening_the_file_leaves_live_runs_alone(tmp_path):
    """`eki history` must not declare the engine's work dead."""
    engine_side = RunStore(tmp_path / "runs.db", owner=True)
    rid = engine_side.create("in flight")
    engine_side.update(rid, state="running")

    RunStore(tmp_path / "runs.db").close()          # a CLI reading, say
    assert engine_side.get(rid)["state"] == "running"


def test_old_jobs_are_adopted_as_runs(tmp_path):
    import sqlite3
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT, prompt TEXT, backend TEXT, reason TEXT,"
                 " cwd TEXT, state TEXT, output TEXT, error TEXT, created_at INTEGER,"
                 " started_at INTEGER, ended_at INTEGER)")
    conn.execute("INSERT INTO jobs VALUES ('j1','old job','claude','','','done',"
                 "'hi',NULL,1,1,2)")
    conn.commit()
    conn.close()
    assert RunStore(db).get("j1")["prompt"] == "old job"


# ---- joining mid-run ------------------------------------------------------

@pytest.mark.parametrize("seen,text,end,expected", [
    (0, "hello", 5, "hello"),         # nothing read yet: all of it
    (5, "hello", 5, ""),              # already in the log
    (3, "hello", 5, "lo"),            # straddles what was read
    (5, " world", 11, " world"),      # entirely new
    (11, "x", 6, ""),                 # an old chunk arriving late
])
def test_unseen_trims_exactly_what_the_log_already_delivered(seen, text, end, expected):
    assert unseen(seen, text, end) == expected


# ---- the runner -----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_run_records_route_and_output(store):
    async def dispatch(run):
        yield {"backend": "qwen", "reason": "cheapest fit"}
        yield "hello "
        yield run["prompt"]

    runner = Runner(store, dispatch)
    rid = store.create("world")
    await runner.submit(rid)
    run = await settle(store, rid)
    assert run["state"] == "done"
    assert run["output"] == "hello world"
    assert run["backend"] == "qwen"


@pytest.mark.asyncio
async def test_runs_execute_in_parallel(store):
    async def dispatch(run):
        await asyncio.sleep(0.3)
        yield "done"

    runner = Runner(store, dispatch)
    ids = [store.create(f"r{i}") for i in range(4)]
    started = time.monotonic()
    for rid in ids:
        await runner.submit(rid)
    for rid in ids:
        await settle(store, rid)
    # four 0.3s runs finishing in well under 1.2s means they overlapped
    assert time.monotonic() - started < 0.8


@pytest.mark.asyncio
async def test_a_failure_reports_its_reason_before_it_ends(store):
    async def dispatch(run):
        yield "partial"
        raise RuntimeError("backend exploded")

    runner = Runner(store, dispatch)
    rid = store.create("x")
    queue = runner.subscribe(rid)
    await runner.submit(rid)

    events = []
    while not events or events[-1].get("state") not in ("failed", "done"):
        events.append(await asyncio.wait_for(queue.get(), timeout=2))
    kinds = [e.get("event") for e in events]
    assert kinds.index("error") < len(kinds) - 1      # error, then the end
    run = await settle(store, rid)
    assert run["output"] == "partial"
    assert "exploded" in run["error"]


@pytest.mark.asyncio
async def test_cancel_stops_it_keeps_output_and_runs_cleanup(store):
    cleaned = asyncio.Event()

    async def dispatch(run):
        try:
            for i in range(1000):
                yield f"{i} "
                await asyncio.sleep(0.01)
        finally:
            cleaned.set()                 # stands in for killing the child CLI

    runner = Runner(store, dispatch)
    rid = store.create("count")
    await runner.submit(rid)
    await asyncio.sleep(0.08)
    assert runner.cancel(rid) is True
    run = await settle(store, rid)
    assert run["state"] == "cancelled"
    assert run["output"]
    await asyncio.wait_for(cleaned.wait(), timeout=1)


@pytest.mark.asyncio
async def test_output_events_carry_a_running_offset(store):
    async def dispatch(run):
        yield "ab"
        yield "cde"

    runner = Runner(store, dispatch)
    rid = store.create("x")
    queue = runner.subscribe(rid)
    await runner.submit(rid)
    ends = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event.get("event") == "output":
            ends.append(event["end"])
        if event.get("state") == "done":
            break
    assert ends == [2, 5]


@pytest.mark.asyncio
async def test_stop_leaves_nothing_running(store, tmp_path):
    """Shutdown kills every task — and a run it cut off is the next engine's
    to call interrupted (and carry on), not a run someone cancelled."""
    async def dispatch(run):
        await asyncio.sleep(10)
        yield "never"

    runner = Runner(store, dispatch)
    rid = store.create("x")
    await runner.submit(rid)
    await asyncio.sleep(0.02)
    await runner.stop()
    assert runner.running == []
    assert store.get(rid)["state"] == "running"
    store.close()
    after = RunStore(tmp_path / "runs.db", owner=True)          # the next engine
    assert after.get(rid)["state"] == "interrupted"
    assert [r["id"] for r in after.just_interrupted] == [rid]
    after.close()


@pytest.mark.asyncio
async def test_cancel_is_still_cancel(store):
    async def dispatch(run):
        await asyncio.sleep(10)
        yield "never"

    runner = Runner(store, dispatch)
    rid = store.create("x")
    await runner.submit(rid)
    await asyncio.sleep(0.02)
    runner.cancel(rid)
    await asyncio.sleep(0.05)
    assert store.get(rid)["state"] == "cancelled"


# ---- the engine: work happens whether anyone is watching or not ----------

@register("echo")
class Echo(Backend):
    """Answers with what it was asked, and a session id unique to the call."""

    counter = 0

    async def health(self):
        return Health(True, "echo")

    async def stream(self, messages, **kw):
        Echo.counter += 1
        mine = Echo.counter
        self.last_session = f"session-{mine}"
        await asyncio.sleep(0.05 * (3 - mine % 3))    # finish out of order
        yield f"[{len(messages)} msgs] {messages[-1].content}"
        self.last_session = f"session-{mine}"          # set late, like the CLIs


def echo_config(tmp_path) -> Config:
    cfg = Config(db_path=str(tmp_path / "eki.db"), quota_url="http://127.0.0.1:1")
    cfg.backends = [BackendInfo(key="echo", kind="echo", label="echo",
                                capabilities=Capabilities(context_tokens=10_000),
                                cost=Cost(tier=0))]
    cfg.options = {"echo": {}}
    return cfg


@pytest.mark.asyncio
async def test_ask_answers_with_nobody_watching(tmp_path):
    eng = Engine(echo_config(tmp_path), owner=True)
    started = await eng.ask("ping")
    # the question is in the thread before any answer exists
    first = eng.store.turns(started["conversation"])
    assert [t["role"] for t in first] == ["user"]

    await settle(eng.runs, started["run"])
    turns = eng.store.turns(started["conversation"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["content"].endswith("ping")
    assert f'"run": "{started["run"]}"' in turns[1]["meta"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_parallel_runs_see_only_the_history_before_their_question(tmp_path):
    eng = Engine(echo_config(tmp_path), owner=True)
    first = await eng.ask("one")
    cid = first["conversation"]
    second = await eng.ask("two", conversation=cid)
    await settle(eng.runs, first["run"])
    await settle(eng.runs, second["run"])

    answers = {eng.runs.get(r)["prompt"]: eng.runs.get(r)["output"]
               for r in (first["run"], second["run"])}
    assert answers["one"].startswith("[1 msgs]")   # not the later question
    assert answers["two"].startswith("[2 msgs]")   # both questions, no answers yet
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_folder_answer_remembers_its_folder(tmp_path):
    """The chat's "Review changes" button reads this off the stored turn."""
    cfg = echo_config(tmp_path)
    cfg.backends[0].capabilities.repo = True
    cfg.backends[0].capabilities.tools = True
    eng = Engine(cfg, owner=True)
    started = await eng.ask("tidy it", repo=str(tmp_path))
    await settle(eng.runs, started["run"])
    answer = eng.store.turns(started["conversation"])[-1]
    assert f'"cwd": "{tmp_path}"' in answer["meta"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_retry_reuses_the_question_instead_of_repeating_it(tmp_path):
    eng = Engine(echo_config(tmp_path), owner=True)
    started = await eng.ask("ping")
    await settle(eng.runs, started["run"])
    eng.runs.update(started["run"], state="interrupted")    # as if the engine died

    again = await eng.retry(started["run"])
    await settle(eng.runs, again["run"])
    roles = [t["role"] for t in eng.store.turns(started["conversation"])]
    assert roles.count("user") == 1
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_finished_run_cannot_be_retried(tmp_path):
    eng = Engine(echo_config(tmp_path), owner=True)
    started = await eng.ask("ping")
    await settle(eng.runs, started["run"])
    assert await eng.retry(started["run"]) is None
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_parallel_runs_do_not_share_an_adapter(tmp_path):
    """Each run keeps its own session id, however they interleave."""
    eng = Engine(echo_config(tmp_path), owner=True)
    Echo.counter = 0
    starts = [await eng.ask(f"q{i}") for i in range(3)]
    for s in starts:
        await settle(eng.runs, s["run"])
    sessions = {eng.store.session(s["conversation"], "echo") for s in starts}
    assert len(sessions) == 3
    await eng.runner.stop()


# ---- a picture, then a word about it --------------------------------------

@register("painter")
class Painter(Backend):
    """Draws nothing, but says what it was asked to draw and from what."""

    calls = []
    out = ""

    async def health(self):
        return Health(True, "painter")

    async def stream(self, messages, **kw):
        Painter.calls.append(dict(kw))
        path = Path(Painter.out) / f"pic{len(Painter.calls)}.png"
        path.write_bytes(b"png")
        yield f"\n![pic]({path})\n"


def studio(tmp_path) -> Engine:
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(
        key="flux", kind="painter", label="flux",
        capabilities=Capabilities(text=False, images_out=True), cost=Cost(tier=0)))
    cfg.options["flux"] = {}
    Painter.calls, Painter.out = [], str(tmp_path)
    return Engine(cfg, owner=True)


async def say(eng, prompt, cid=""):
    started = await eng.ask(prompt, conversation=cid)
    await settle(eng.runs, started["run"])
    return started["conversation"], eng.store.turns(started["conversation"])[-1]


@pytest.mark.asyncio
async def test_a_change_to_a_picture_goes_back_to_the_image_model(tmp_path):
    eng = studio(tmp_path)
    cid, first = await say(eng, "generate an image of a cat")
    assert first["backend"] == "flux"

    _, second = await say(eng, "make it bluer", cid)
    assert second["backend"] == "flux"
    assert Painter.calls[1]["edit"] == str(tmp_path / "pic1.png")
    assert Painter.calls[1]["prompt"] == "make it bluer"

    # and the edit is itself a picture, so the next change builds on it
    _, third = await say(eng, "remove the background", cid)
    assert third["backend"] == "flux"
    assert Painter.calls[2]["edit"] == str(tmp_path / "pic2.png")
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_try_again_repeats_what_made_the_picture(tmp_path):
    eng = studio(tmp_path)
    cid, _ = await say(eng, "generate an image of a cat")
    await say(eng, "try again", cid)
    assert Painter.calls[1]["prompt"] == "generate an image of a cat"
    assert "edit" not in Painter.calls[1]

    await say(eng, "make it bluer", cid)
    await say(eng, "another one", cid)              # the edit again, not a new cat
    assert Painter.calls[3]["prompt"] == "make it bluer"
    assert Painter.calls[3]["edit"] == Painter.calls[2]["edit"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_words_after_a_picture_are_still_words(tmp_path):
    eng = studio(tmp_path)
    cid, _ = await say(eng, "generate an image of a cat")
    _, answer = await say(eng, "what model did you use?", cid)
    assert answer["backend"] == "echo"
    # the thread has moved on: "make it shorter" is now about the words
    _, later = await say(eng, "make it shorter", cid)
    assert later["backend"] == "echo"
    await eng.runner.stop()
