# SPDX-License-Identifier: Apache-2.0
"""eki watching itself: what counts as its own fault, what's worth a fix
proposal, and that noticing never gets in the way of the work."""
import asyncio
import json
import logging
import time

import pytest

from eki import observe
from eki.adapters.base import Backend, BackendError, BackendInfo, Capabilities, Cost, Health, register
from eki.engine import Engine
from tests.test_runs import echo_config, settle


def raised_in_eki():
    """An error raised inside eki's own code."""
    from eki import skills
    try:
        skills._check_name("../bad")                     # ValueError in eki/skills.py
    except ValueError as e:
        return e


# ---- what's a fault ------------------------------------------------------------------

def test_an_error_in_ekis_code_is_a_fault_and_a_provider_saying_no_isnt():
    e = raised_in_eki()
    assert observe.is_fault(e)
    assert observe.signature(e) == "ValueError in eki/skills.py:_check_name"
    assert not observe.is_fault(BackendError("claude: rate limited"))
    try:
        raise KeyError("x")                               # never passed through eki
    except KeyError as e2:
        assert not observe.is_fault(e2)


def test_a_fault_is_journalled_with_where_and_versions(monkeypatch):
    monkeypatch.setattr(observe, "versions", lambda: {"codex": "0.154.0"})
    entry = observe.fault(raised_in_eki(), source="run", run="r1", backend="codex", request="hi")
    assert entry["file"] == "eki/skills.py" and entry["line"] > 0
    assert "Traceback" in entry["traceback"] and entry["versions"] == {"codex": "0.154.0"}
    assert observe.entries(kind="fault")[0]["signature"] == entry["signature"]
    assert observe.fault(BackendError("no"), source="run") is None


def test_the_log_handler_catches_what_the_engines_loops_log(monkeypatch):
    monkeypatch.setattr(observe, "versions", lambda: {})
    handler = observe.FaultLog()
    got = []
    handler.on_fault = got.append
    log = logging.getLogger("eki.test.observe")
    log.addHandler(handler)
    try:
        try:
            raise raised_in_eki()
        except ValueError:
            log.exception("idle reaper")
        log.error("no traceback, not a fault")
    finally:
        log.removeHandler(handler)
    assert len(got) == 1 and got[0]["log"] == "idle reaper"


# ---- what gets a proposal --------------------------------------------------------------

def _fault(source="run", file="eki/adapters/codex.py", sig="KeyError in eki/adapters/codex.py:stream"):
    return observe.note("fault", signature=sig, source=source, file=file, line=10,
                        error="KeyError: 'item'", traceback="Traceback…")


def test_twice_before_a_proposal_once_if_an_engine_loop_broke():
    e = _fault()
    assert "seen 1 of 2" in observe.due(e)
    e = _fault()
    assert observe.due(e) == ""
    loop = _fault(source="engine", sig="TypeError in eki/models.py:reap_idle", file="eki/models.py")
    assert observe.due(loop) == ""


def test_not_again_this_week_and_not_more_than_a_few_a_day():
    e = _fault(); e = _fault()
    observe.mark(e["signature"], state="proposed", at=int(time.time()))
    assert "this week" in observe.due(e)
    for i in range(3):
        observe.mark(f"other {i}", state="proposed", at=int(time.time()))
    x = _fault(sig="KeyError in eki/x.py:f", file="eki/x.py"); x = _fault(sig="KeyError in eki/x.py:f", file="eki/x.py")
    assert "3 proposals today" in observe.due(x)


def test_the_machinery_that_would_fix_it_and_protected_paths_go_to_a_person():
    for f in ("eki/selfwork.py", "eki/builds.py", "eki/observe.py"):
        e = _fault(source="engine", sig=f"E in {f}:g", file=f)
        assert "for a person" in observe.due(e)
    e = _fault(source="engine", sig="E in eki/secrets.py:g", file="eki/secrets.py")
    assert "protected" in observe.due(e)


def test_the_brief_carries_the_evidence():
    e = _fault(); e2 = _fault()
    text = observe.brief({**e, "versions": {"codex": "0.155.0"}, "backend": "codex",
                          "request": "make a file"}, [e, e2], "abc123 2026-09-20 codex: new events")
    assert "KeyError: 'item'" in text and "2 time(s)" in text and "codex 0.155.0" in text
    assert "Traceback" in text and "abc123" in text and "add a test" in text


# ---- in the engine ------------------------------------------------------------------------

@register("glitch")
class Glitch(Backend):
    """A backend whose own parsing breaks — the kind of thing a CLI update does."""
    mode = "bug"

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        if Glitch.mode == "provider":
            raise BackendError("codex: usage limit reached")
        yield "partial"
        from eki import skills
        skills._check_name("../not a name")              # an error inside eki's code


def clinic(tmp_path, monkeypatch, **settings):
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(key="glitch", kind="glitch", label="glitch",
                                    capabilities=Capabilities(), cost=Cost(tier=0)))
    cfg.options["glitch"] = {}
    eng = Engine(cfg, owner=True)
    eng.settings = {**eng.settings, "skills_learn": "off", "notify_learned": False,
                    "self_fix": "propose", **settings}
    monkeypatch.setattr(observe, "versions", lambda: {})
    # running from your checkout, wherever these tests run — a build's own
    # candidate check runs them inside the build, whose commit is not HEAD
    from eki import builds
    monkeypatch.setattr(builds, "running", lambda: {"dev": True})
    return eng


async def quiet(eng):
    for _ in range(200):
        if not eng._side_tasks:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_fault_twice_gets_a_fix_proposal_once(tmp_path, monkeypatch):
    from eki import selfloop
    eng = clinic(tmp_path, monkeypatch)
    Glitch.mode = "bug"
    started = []

    async def start(it, goal=None, allowed=None):              # the self-work run, stood in for
        started.append(it)
        return {"run": "r9", "conversation": "c9"}
    monkeypatch.setattr(eng, "_self_start", start)
    for _ in range(3):
        ran = await eng.ask("do it", backend_key="glitch")
        assert (await settle(eng.runs, ran["run"]))["state"] == "failed"
        await quiet(eng)
    assert len(started) == 1                                    # the second time; not the third
    it = started[0]
    assert it.source == "fault" and "ValueError" in it.request and "Traceback" in it.request
    assert it.base == "HEAD" and not it.check_base              # the running code, broken as it is
    sig = "ValueError in eki/skills.py:_check_name"
    assert observe.proposals()[sig]["state"] == "working" and observe.proposals()[sig]["item"] == it.id
    assert selfloop.get(it.id).key == sig
    assert observe.summary()["faults"][0]["count"] == 3
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_with_eki_working_on_itself_a_fault_waits_for_the_loop(tmp_path, monkeypatch):
    from eki import goals, selfloop
    eng = clinic(tmp_path, monkeypatch)
    Glitch.mode = "bug"
    goals.create(selfloop.GOAL_TEXT, spare=True, kind="self")
    monkeypatch.setattr(eng, "_self_start", lambda *a, **k: pytest.fail("the loop takes it, not now"))
    for _ in range(2):
        ran = await eng.ask("do it", backend_key="glitch")
        await settle(eng.runs, ran["run"])
        await quiet(eng)
    queued = [i for i in selfloop.items() if i.source == "fault"]
    assert len(queued) == 1 and queued[0].state == "queued"
    assert observe.proposals()["ValueError in eki/skills.py:_check_name"]["state"] == "queued"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_provider_saying_no_is_journalled_but_never_fixed(tmp_path, monkeypatch):
    from eki import selfwork
    eng = clinic(tmp_path, monkeypatch)
    Glitch.mode = "provider"
    monkeypatch.setattr(selfwork, "propose", lambda *a, **k: pytest.fail("not eki's fault"))
    for _ in range(3):
        started = await eng.ask("do it", backend_key="glitch")
        await settle(eng.runs, started["run"])
        await quiet(eng)
    rows = observe.entries(kind="failed")
    assert len(rows) == 3 and "usage limit" in rows[0]["error"] and not observe.entries(kind="fault")
    Glitch.mode = "bug"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_self_fix_off_still_journals(tmp_path, monkeypatch):
    from eki import selfwork
    eng = clinic(tmp_path, monkeypatch, self_fix="off")
    monkeypatch.setattr(selfwork, "propose", lambda *a, **k: pytest.fail("off"))
    for _ in range(2):
        started = await eng.ask("do it", backend_key="glitch")
        await settle(eng.runs, started["run"])
        await quiet(eng)
    assert len(observe.entries(kind="fault")) == 2
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_correction_is_friction(tmp_path, monkeypatch):
    eng = clinic(tmp_path, monkeypatch)
    first = await eng.ask("write a haiku about rain")
    await settle(eng.runs, first["run"])
    second = await eng.ask("no, make it about snow", conversation=first["conversation"])
    await settle(eng.runs, second["run"])
    rows = observe.entries(kind="friction")
    assert len(rows) == 1 and rows[0]["signal"] == "corrected" and rows[0]["before"] == "echo"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_request_nothing_could_take_is_a_gap(tmp_path, monkeypatch):
    eng = clinic(tmp_path, monkeypatch)
    started = await eng.ask("do it", backend_key="no-such-backend")
    await settle(eng.runs, started["run"])
    assert observe.entries(kind="gap") and observe.entries(kind="failed")
    await eng.runner.stop()


def test_removing_a_learned_skill_is_history():
    from eki import skills
    skills.learn("use-pnpm", "JS projects here use pnpm, never npm.", "Use pnpm install.", why="w")
    skills.remove("use-pnpm")
    rows = observe.entries(kind="history")
    assert rows and rows[0]["skill"] == "use-pnpm"
