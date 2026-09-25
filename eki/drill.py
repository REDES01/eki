"""The restart drill: proof of "a restart at any moment loses nothing".

In a throwaway EKI_HOME, with the fake provider:

1. a long run starts, and a second is queued behind it in the same thread;
2. the engine is killed hard — the worker must keep working;
3. a third run is asked for while no engine is up;
4. a new engine starts, and the worker is killed hard mid-turn — the run
   must be resumed in the same session, not started over or failed;
5. a command run (a check, say) is killed mid-way — it must be run again
   and finish exactly once;
6. everything must end `done`, nothing failed, nothing doubled.
"""
from __future__ import annotations

import os
import sys
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
            cmd = store.create_run(c, store.create_thread(c, "a command", home),
                                   '["sh", "-c", "for i in 1 2 3 4 5 6 7 8; do echo tick $i; '
                                   'sleep 0.3; done; echo cmd done"]', provider="command")
        ok("a request was taken while no engine was up")

        engine.start_detached()
        _wait("a new engine", lambda: engine.running_pid() is not None)
        worker_pid = store.run(c, long_run)["pid"]
        os.kill(worker_pid, signal.SIGKILL)
        ok("new engine up; the worker killed mid-turn")

        _wait("the command to get going", lambda: "tick 2" in store.answer(c, cmd))
        os.kill(store.run(c, cmd)["pid"], signal.SIGKILL)
        ok("a command run killed mid-way")

        _wait("everything to finish", lambda: all(
            store.run(c, r)["state"] in ("done", "failed", "cancelled")
            for r in (long_run, second, third, cmd)), timeout=60)
        for r in (long_run, second, third, cmd):
            row = store.run(c, r)
            assert row["state"] == "done", f"run {r} ended {row['state']}: {row['error']}"
        assert store.answer(c, cmd).count("cmd done") == 1, "the command didn't finish exactly once"
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


# ---- the swap drill ------------------------------------------------------------------------

def swaps(say: Callable[[str], None] = print, home: str | None = None) -> List[str]:
    """The go-live path, for real: the launcher runs an engine from a build; a
    swap to a new build mid-run changes the engine and not the run; a build
    that can't start is rolled back by the launcher."""
    import subprocess
    from . import builds
    home = home or tempfile.mkdtemp(prefix="eki-swapdrill-")
    os.makedirs(home, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("EKI_HOME", "EKI_MACHINE", "EKI_PORT", "EKI_WATCH")}
    os.environ.update({"EKI_HOME": home, "EKI_MACHINE": "ok", "EKI_PORT": "0", "EKI_WATCH": "3"})
    checks: List[str] = []
    launcher = None

    def ok(text: str) -> None:
        checks.append(text)
        say(f"  ✓ {text}")

    def swap_state() -> tuple:
        st = builds.status()
        return (st.get("swap") or {}).get("state"), (st.get("swap") or {}).get("target")

    try:
        with open(os.path.join(home, "providers.json"), "w") as f:
            f.write(PROVIDERS)
        with open(os.path.join(home, "routing.json"), "w") as f:
            f.write(ROUTING)
        first = builds.make(builds.source(), None)
        builds.swap_to(first, "drill: first build")
        env = {**os.environ, "EKI_PYTHON": sys.executable}
        launcher = subprocess.Popen(["/bin/sh", str(builds.launcher_source())], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        c = db.connect()
        _wait("the launcher's engine", lambda: engine.running_pid() is not None)
        pid1 = engine.running_pid()
        ok("the launcher started an engine from the first build")

        with db.tx(c):
            rid = store.create_run(c, store.create_thread(c, "swap", None), "steps=30 delay=0.2 across")
        _wait("the run to get going",
              lambda: len([e for e in store.events_after(c, rid) if e["kind"] == "text"]) >= 2)
        worker = store.run(c, rid)["pid"]
        second = builds.make(builds.source(), None)
        builds.swap_to(second, "drill: second build")
        _wait("a new engine", lambda: engine.running_pid() not in (None, pid1))
        ok("swapped: a new engine from the second build")
        _wait("the second build to be healthy", lambda: swap_state() == ("healthy", str(second)), 20)
        ok("healthy after the watch window")
        _wait("the run to finish", lambda: store.run(c, rid)["state"] == "done", 30)
        r = store.run(c, rid)
        assert r["pid"] == worker and r["attempt"] == 1, "the swap disturbed the run"
        assert store.answer(c, rid).count("fake done:") == 1
        ok("the run went on in its worker through the swap and finished once")

        bad = builds.root() / "bad"
        (bad / "eki").mkdir(parents=True)
        (bad / "eki" / "__init__.py").write_text("")
        (bad / "eki" / "engine.py").write_text("import sys\nsys.exit(3)\n")
        (bad / ".eki-build.json").write_text('{"id": "bad", "commit": "none", "source": ""}')
        pid2 = engine.running_pid()
        builds.swap_to(bad, "drill: a build that can't start")
        _wait("the rollback", lambda: (builds.status().get("rollback") or {}).get("to") == str(second), 20)
        _wait("an engine again", lambda: engine.running_pid() not in (None, pid2))
        assert builds.current() == second, "current isn't the previous build after the rollback"
        ok("a build that can't start was rolled back by the launcher")
    finally:
        if launcher is not None:
            try:
                os.killpg(launcher.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            _reap(launcher.pid)
        pid = engine.running_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return checks
