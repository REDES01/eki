# SPDX-License-Identifier: Apache-2.0
"""The programs doing the work outlive the engine (eki/workers.py): a
restart leaves them running and the next engine follows them again; one that
finished in the gap is concluded from its files; a pid that came back as
another program isn't taken for a worker; a person's cancel still kills."""
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from eki import steps, workers

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def run(coro):
    return asyncio.run(coro)


def gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.time() + within
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def claude_config(tmp_path, monkeypatch):
    from eki import secrets, settings
    from eki.adapters import claude_code as cc
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="claude", kind="claude_code", label="Claude",
                                capabilities=Capabilities(context_tokens=200000, repo=True, tools=True),
                                cost=Cost(tier=50))]
    cfg.options = {"claude": {"binary": FAKE[0]}}
    real = cc.ClaudeCodeBackend.live_argv
    monkeypatch.setattr(cc.ClaudeCodeBackend, "live_argv",
                        lambda self, cwd, resume, sid: [real(self, cwd, resume, sid)[0], FAKE[1]]
                        + real(self, cwd, resume, sid)[1:])
    monkeypatch.setattr(cc.ClaudeCodeBackend, "_no_bare_flag", lambda self: None)
    return cfg


def engine(cfg):
    from eki.engine import Engine
    eng = Engine(cfg, owner=True)
    eng.settings = {**eng.settings, "skills_learn": "off", "resume_interrupted": True,
                    "claude_tools": False}
    return eng


async def until(check, within: float = 10.0):
    deadline = time.time() + within
    while time.time() < deadline:
        got = check()
        if got:
            return got
        await asyncio.sleep(0.05)
    raise AssertionError("never happened")


async def stop(eng):
    """The engine going away — a restart, a swap: what `serve` does on its way out."""
    await eng.quota.stop()
    await eng.runner.stop()
    await eng.close()


async def restarted(cfg):
    """A new engine on the same database, starting the way `serve` does."""
    eng = engine(cfg)
    kept = eng.reattach_workers()
    eng.note_interruptions()
    await eng.resume_interrupted()
    return eng, kept


def the_run_after(eng, cid, rid):
    return next(r for r in eng.runs.recent() if r["conversation_id"] == cid and r["id"] != rid)


COUNT = "".join(f"{i} " for i in range(1, 31))


def test_a_program_survives_the_engine_going_and_the_thread_streams_on(tmp_path, monkeypatch):
    cfg = claude_config(tmp_path, monkeypatch)

    async def go():
        first = engine(cfg)
        started = await first.ask("count slowly to 30", backend_key="claude")
        rid, cid = started["run"], started["conversation"]
        await until(lambda: "4 " in ((first.runs.get(rid) or {}).get("output") or ""))
        pid = first.live[cid].proc.pid
        await stop(first)
        assert not gone(pid, within=0.5)                # the engine went; the program didn't

        second, kept = await restarted(cfg)
        assert kept == 1 and second.live[cid].proc.pid == pid     # the same process, taken up
        new = the_run_after(second, cid, rid)
        done = await until(lambda: (second.runs.get(new["id"]) or {}).get("state") in ("done", "failed")
                           and second.runs.get(new["id"]))
        assert done["state"] == "done"
        before = second.runs.get(rid)["output"]
        # every word once: what the first engine streamed, then the rest
        assert before + done["output"] == COUNT
        assert json.loads(done["payload"])["resume_of"] == rid
        turns = second.store.turns(cid)
        assert "Carry on where you left off." not in [t["content"] for t in turns]   # nothing was asked again
        assert "following it again" in turns[-2]["content"] and turns[-2]["content"].startswith(before.strip())
        assert turns[-1]["content"].strip() == done["output"].strip()
        assert second.live[cid].proc.pid == pid
        await second.quota.stop()
        await second.runner.stop()
        for s in second.live.values():
            await s.close()
    run(go())


def test_a_turn_that_ended_while_no_engine_was_up_is_concluded(tmp_path, monkeypatch):
    cfg = claude_config(tmp_path, monkeypatch)

    async def go():
        first = engine(cfg)
        started = await first.ask("count slowly to 12", backend_key="claude")
        rid, cid = started["run"], started["conversation"]
        await until(lambda: "2 " in ((first.runs.get(rid) or {}).get("output") or ""))
        w = first.live[cid].worker
        await stop(first)
        # nobody reading: the program finishes its turn on its own
        await until(lambda: w.said_after(0, b'"type": "result"'), within=10)

        second, kept = await restarted(cfg)
        assert kept == 1
        new = the_run_after(second, cid, rid)
        done = await until(lambda: (second.runs.get(new["id"]) or {}).get("state") in ("done", "failed")
                           and second.runs.get(new["id"]))
        assert done["state"] == "done"
        assert second.runs.get(rid)["output"] + done["output"] == "".join(f"{i} " for i in range(1, 13))
        await second.quota.stop()
        await second.runner.stop()
        for s in second.live.values():
            await s.close()
    run(go())


def test_a_program_whose_turn_had_ended_is_not_followed_again(tmp_path, monkeypatch):
    # the restart drill (eki/drill.py): a self-work run whose agent had
    # finished its turn was cut off in its check — the new engine took the
    # idle program for one mid-turn, and wrote its answer into the thread twice
    cfg = claude_config(tmp_path, monkeypatch)

    async def go():
        first = engine(cfg)
        started = await first.ask("count slowly to 6", backend_key="claude")
        rid, cid = started["run"], started["conversation"]
        await until(lambda: (first.runs.get(rid) or {}).get("state") == "done")
        w = first.live[cid].worker
        assert w.spec["turn"] is False                   # its turn ended, and says so
        # the run it served went on to something else (a check), still going
        later = first.runs.create("go on", conversation=cid, requested="claude")
        first.runs.update(later, state="running", backend="claude", started_at=1)
        w.update(run=later)
        await stop(first)

        second, kept = await restarted(cfg)
        assert kept == 1 and cid in second.live          # taken up, idle…
        assert cid not in second._follow_on              # …not followed as if mid-turn
        said = [t["content"] for t in second.store.turns(cid) if t["role"] == "assistant"]
        assert sum(s.count("1 2 3 4 5 6") for s in said) == 1
        assert not any("following it again" in s for s in said)
        await second.quota.stop()
        await second.runner.stop()
        for s in second.live.values():
            await s.close()
    run(go())


def test_a_persons_cancel_still_kills_the_program(tmp_path, monkeypatch):
    cfg = claude_config(tmp_path, monkeypatch)

    async def go():
        eng = engine(cfg)
        started = await eng.ask("count slowly to 200", backend_key="claude")
        rid, cid = started["run"], started["conversation"]
        await until(lambda: "2 " in ((eng.runs.get(rid) or {}).get("output") or ""))
        w = eng.live[cid].worker
        pid = w.pid
        assert eng.runner.cancel(rid)
        await until(lambda: w.exit is not None, within=8)
        assert gone(pid)
        await until(lambda: eng.runs.get(rid)["state"] == "cancelled")
        await stop(eng)
    run(go())


def test_a_worker_left_by_an_engine_is_joined_not_started_twice(tmp_path):
    marker = tmp_path / "ran"
    cmd = [sys.executable, "-c",
           f"import time; open({str(marker)!r}, 'a').write('x'); time.sleep(0.8); print('3 passed')"]
    # the engine before this one started it, then went away
    w = workers.start(cmd, key="k1", kind="tests")
    workers.release(w.id)
    code, out, _ = workers.run(cmd, key="k1")
    assert code == 0 and out.strip() == "3 passed"
    assert marker.read_text() == "x"                    # one run, not two
    assert workers.find(key="k1")[0].spec.get("collected")


def test_a_result_that_came_while_no_engine_was_up_is_taken(tmp_path):
    marker = tmp_path / "ran"
    cmd = [sys.executable, "-c", f"open({str(marker)!r}, 'a').write('x'); print('ok'); raise SystemExit(1)"]
    w = workers.start(cmd, key="k2")
    workers.release(w.id)
    deadline = time.time() + 10
    while w.exit is None and time.time() < deadline:
        time.sleep(0.05)
    assert w.exit == 1
    code, out, _ = workers.run(cmd, key="k2")           # the step taken up again
    assert (code, out.strip(), marker.read_text()) == (1, "ok", "x")
    # collected: the next check of the same thing runs again
    code, _, _ = workers.run(cmd, key="k2")
    assert code == 1 and marker.read_text() == "xx"


def test_a_worker_lost_with_nothing_written_is_cut_off_not_failed(tmp_path):
    import threading
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    got = {}
    waiting = threading.Thread(target=lambda: got.update(r=workers.run(cmd, key="k3")))
    waiting.start()
    deadline = time.time() + 10
    while not (workers.find(key="k3") and workers.find(key="k3")[0].pid) and time.time() < deadline:
        time.sleep(0.05)
    w = workers.find(key="k3")[0]
    os.killpg(w.group, signal.SIGKILL)                  # keeper and program, no word from either
    waiting.join(10)
    assert w.lost() and w.exit is None
    code = got["r"][0]
    assert code == -9 and steps.cut_off(code)           # interrupted, never "tests failed"


def test_a_pid_that_came_back_as_another_program_is_not_a_worker(tmp_path, monkeypatch):
    d = workers.HOME / "reused"
    d.mkdir(parents=True)
    # this very process holds the pid now — alive, but not what was started
    (d / "spec.json").write_text(json.dumps({"argv": ["claude"], "kind": "claude", "thread": "t1",
                                             "at": time.time() - 3600}))
    (d / "state.json").write_text(json.dumps({"pid": os.getpid(), "keeper": 0,
                                              "started": "Mon Jan  1 00:00:00 2001"}))
    w = workers.Worker(d)
    assert not w.alive() and w.lost()
    # nothing reattaches it, and housekeeping never kills the process holding the pid
    killed = []
    monkeypatch.setattr(workers.Worker, "signal", lambda self, sig: killed.append(sig))
    assert workers.sweep(lambda w: False, now=time.time() + 3600)["killed"] == 0
    assert killed == []
    from eki.config import Config
    from eki.engine import Engine
    monkeypatch.setattr("eki.settings.PATH", tmp_path / "settings.json")
    eng = Engine(Config(db_path=str(tmp_path / "eki.db")), owner=True)
    assert eng.reattach_workers() == 0 and "t1" not in eng.live


def test_housekeeping_forgets_old_work_and_kills_an_orphan_after_its_grace(tmp_path):
    old = workers.start([sys.executable, "-c", "print(1)"], key="old")
    deadline = time.time() + 10
    while old.exit is None and time.time() < deadline:
        time.sleep(0.05)
    orphan = workers.start([sys.executable, "-c", "import time; time.sleep(60)"], key="orphan", run="gone")
    while not orphan.pid and time.time() < deadline:
        time.sleep(0.05)
    workers.release(orphan.id)                          # left by an engine before this one
    noted = []
    note = lambda kind, **f: noted.append((kind, f))   # noqa: E731
    now = time.time()
    got = workers.sweep(lambda w: False, now=now, note=note)
    assert got == {"removed": 0, "killed": 0}           # an orphan gets its grace
    got = workers.sweep(lambda w: False, now=now + workers.ORPHAN_GRACE + 1, note=note)
    assert got["killed"] == 1 and gone(orphan.pid)
    assert noted and noted[0][1]["what"] == "orphan worker killed" and noted[0][1]["run"] == "gone"
    got = workers.sweep(lambda w: False, now=now + workers.KEEP + 60)
    assert got["removed"] == 2 and not workers.scan()


def test_launchd_stops_the_whole_engine_but_not_the_workers(tmp_path):
    """The launcher and the engine under it go together (a killed launcher
    left the engine holding the port, 2026-09-24); the workers are in
    sessions of their own, so they stay."""
    from eki import agent
    assert agent.plist_for(tmp_path)["AbandonProcessGroup"] is False
    w = workers.start([sys.executable, "-c", "import time; time.sleep(30)"], key="own", run="r")
    deadline = time.time() + 10
    while not w.pid and time.time() < deadline:
        time.sleep(0.05)
    assert os.getpgid(w.pid) != os.getpgid(0)
    w.kill()


def test_the_watchdog_asks_the_supervisor_every_half_minute():
    from eki import agent, builds
    job = agent.watchdog_plist()
    assert job["ProgramArguments"] == ["/bin/bash", str(builds.SUPERVISOR), "--watchdog"]
    assert job["StartInterval"] == agent.WATCHDOG_EVERY == 30


# ---- a program that stops reading its input ----------------------------------------------

DEAF = [sys.executable, "-c", "import time; time.sleep(60)"]


async def _ticking(gaps):
    last = time.monotonic()
    while True:
        await asyncio.sleep(0.02)
        now = time.monotonic()
        gaps.append(now - last)
        last = now


def test_the_keeper_drains_what_a_busy_program_hasnt_read(tmp_path):
    """2026-09-24: the engine wrote to a program that wasn't reading, the
    fifo filled, and one blocking write froze the whole engine. The keeper
    now takes it all in, and the engine never waits."""
    async def go():
        proc = await workers.start_proc(DEAF, None, None, kind="test")
        gaps = []
        tick = asyncio.create_task(_ticking(gaps))
        for _ in range(32):
            proc.stdin.write(b"x" * 65536)              # 2 MB the program reads none of
        await until(lambda: proc.stdin.waiting == 0, within=10)
        tick.cancel()
        assert proc.returncode is None and not proc.stalled
        assert max(gaps) < 0.5                          # the loop never stood still
        proc.worker.kill()
    run(go())


def test_a_program_that_takes_none_of_its_input_is_stopped_not_waited_on(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "INPUT_CAP", 64 * 1024)   # the keeper holds little
    monkeypatch.setattr(workers, "STALL", 1.0)

    async def go():
        proc = await workers.start_proc(DEAF, None, None, kind="test")
        proc.stdin.cap = 64 << 20                           # the engine would wait on more
        gaps = []
        tick = asyncio.create_task(_ticking(gaps))
        began = time.monotonic()
        proc.stdin.write(b"x" * (1 << 20))
        assert time.monotonic() - began < 0.5
        assert proc.stdin.waiting > 0
        code = await asyncio.wait_for(proc.wait(), timeout=10)
        tick.cancel()
        assert max(gaps) < 0.5
        assert "took none of its input" in proc.stalled
        assert steps.cut_off(code)                          # stopped: cut off, not failed
        with pytest.raises(workers.InputStalled):
            proc.stdin.write(b"more\n")
    run(go())


def test_too_much_waiting_input_stops_it_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "INPUT_CAP", 64 * 1024)

    async def go():
        proc = await workers.start_proc(DEAF, None, None, kind="test")
        with pytest.raises(workers.InputStalled):
            for _ in range(64):
                proc.stdin.write(b"x" * 65536)
        assert "KB of its input waiting" in proc.stalled
        await asyncio.wait_for(proc.wait(), timeout=10)
    run(go())


def test_a_stalled_program_is_cut_off_for_the_session_too():
    """What a session makes of it: an interruption (its step taken up again),
    not "Claude Code is not reading" as a failure."""
    from eki import codex_live, live

    class Stdin:
        def write(self, data):
            raise workers.InputStalled("the worker stopped reading: it took none of its input for 60s")

    class P:
        returncode = None
        stdin = Stdin()
    for session in (live.LiveSession(["claude"], None, None), codex_live.CodexSession(["codex"], None, None)):
        session.proc = P()
        with pytest.raises(steps.Interrupted):
            session._write({"type": "user"})
