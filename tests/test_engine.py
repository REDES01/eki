import os

import pytest

from eki import db, drill, engine, store


@pytest.mark.drill
def test_restart_drill(home):
    checks = drill.run(say=lambda s: None, home=str(home / "drill"))
    assert len(checks) == 7


@pytest.mark.drill
def test_swap_drill(home):
    checks = drill.swaps(say=lambda s: None, home=str(home / "swaps"))
    assert len(checks) == 5


def test_runs_of_one_thread_go_one_at_a_time(conn, monkeypatch):
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    with db.tx(conn):
        t = store.create_thread(conn, "t", None)
        a = store.create_run(conn, t, "a")
        store.create_run(conn, t, "b")
        other = store.create_run(conn, store.create_thread(conn, "u", None), "c")
    engine.spawn_ready(conn)
    assert spawned == [a, other]


def test_background_waits_for_room(conn, monkeypatch):
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    monkeypatch.setenv("EKI_MACHINE", "busy")
    with db.tx(conn):
        store.create_run(conn, store.create_thread(conn, "t", None), "x", priority="background")
        now = store.create_run(conn, store.create_thread(conn, "u", None), "y")
    engine.spawn_ready(conn)
    assert spawned == [now]


def test_a_dead_worker_is_requeued_and_a_stuck_one_gives_up(conn):
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), "x")
    store.update_run(conn, rid, state="running", pid=999999, heartbeat=1.0, attempt=1)
    engine.reap(conn)
    assert store.run(conn, rid)["state"] == "queued"
    store.update_run(conn, rid, state="running", pid=999999, heartbeat=1.0, attempt=engine.MAX_ATTEMPTS)
    engine.reap(conn)
    r = store.run(conn, rid)
    assert r["state"] == "failed" and "interrupted" in r["error"]


def test_only_one_engine(home):
    held = engine.lock()
    assert held is not None
    assert engine.lock() is None
    assert engine.running_pid() == os.getpid()
    held.close()


def test_the_engine_sees_exit_codes(home):
    """With SIGCHLD ignored, subprocess.run would report 0 for everything (the
    2026-09-26 bug: every git question the engine asked was answered yes)."""
    import subprocess
    import sys
    code = ("from eki import engine, db\n"
            "import subprocess\n"
            "import signal; signal.signal(signal.SIGCHLD, signal.SIG_IGN); engine.handle_signals([])\n"
            "engine.tick(db.connect())\n"
            "print(subprocess.run(['/bin/sh', '-c', 'exit 3']).returncode)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "EKI_PORT": "0"})
    assert out.stdout.strip() == "3", out.stderr
