"""The restart drill: proof of "a restart at any moment loses nothing".

In a throwaway EKI_HOME, with the fake provider:

1. a long run starts, and a second is queued behind it in the same thread;
2. the engine is killed hard — the worker must keep working;
3. a third run is asked for while no engine is up;
4. a new engine starts, and the worker is killed hard mid-turn — the run
   must be resumed in the same session, not started over or failed;
5. everything must end `done`, nothing failed, nothing doubled.
"""
from __future__ import annotations

import os
import signal
import tempfile
import time
from typing import Callable, List

from . import db, engine, store

PROVIDERS = '{"fake": {"kind": "fake"}}'
ROUTING = '{"checker": "none", "rows": [{"key": "general", "title": "all", "targets": ["fake"]}]}'


def _wait(what: str, cond: Callable[[], bool], timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _reap(pid: int) -> None:
    """The drill started this engine, so it must collect it, or it lingers as a zombie."""
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def run(say: Callable[[str], None] = print, home: str | None = None) -> List[str]:
    home = home or tempfile.mkdtemp(prefix="eki-drill-")
    os.makedirs(home, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("EKI_HOME", "EKI_STALE", "EKI_MACHINE", "EKI_PORT")}
    os.environ.update({"EKI_HOME": home, "EKI_STALE": "2", "EKI_MACHINE": "ok", "EKI_PORT": "0"})
    engine.STALE = 2.0
    checks: List[str] = []

    def ok(text: str) -> None:
        checks.append(text)
        say(f"  ✓ {text}")

    try:
        with open(os.path.join(home, "providers.json"), "w") as f:
            f.write(PROVIDERS)
        with open(os.path.join(home, "routing.json"), "w") as f:
            f.write(ROUTING)
        c = db.connect()
        with db.tx(c):
            tid = store.create_thread(c, "drill", None)
            long_run = store.create_run(c, tid, "steps=25 delay=0.2 long")
            second = store.create_run(c, tid, "steps=2 second")
        engine.start_detached()
        _wait("the long run to get going",
              lambda: len([e for e in store.events_after(c, long_run) if e["kind"] == "text"]) >= 3)
        ok("run started and is writing")

        pid = engine.running_pid()
        os.kill(pid, signal.SIGKILL)
        _reap(pid)
        _wait("the engine to be gone", lambda: not engine.alive(pid))
        before = len(store.events_after(c, long_run))
        time.sleep(1.2)
        assert len(store.events_after(c, long_run)) > before, "worker stopped with the engine"
        ok("engine killed; the worker kept working")

        with db.tx(c):
            third = store.create_run(c, store.create_thread(c, "while down", None), "steps=2 third")
        ok("a request was taken while no engine was up")

        engine.start_detached()
        _wait("a new engine", lambda: engine.running_pid() is not None)
        worker_pid = store.run(c, long_run)["pid"]
        os.kill(worker_pid, signal.SIGKILL)
        ok("new engine up; the worker killed mid-turn")

        _wait("everything to finish", lambda: all(
            store.run(c, r)["state"] in ("done", "failed", "cancelled") for r in (long_run, second, third)),
            timeout=60)
        for r in (long_run, second, third):
            row = store.run(c, r)
            assert row["state"] == "done", f"run {r} ended {row['state']}: {row['error']}"
        lr = store.run(c, long_run)
        assert lr["attempt"] == 2, f"the long run took {lr['attempt']} attempts"
        said = store.answer(c, long_run)
        assert said.count("fake done:") == 1, "the long run finished more than once"
        assert "step 1/25" in said and said.count("step 1/25") == 1, "the long run started over"
        ok("the killed run resumed in its session and finished once")
        ok("the queued and the offline requests finished")
    finally:
        pid = engine.running_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            _reap(pid)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return checks
