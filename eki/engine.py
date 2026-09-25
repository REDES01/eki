"""The engine: a manager, not a parent.

Every half second it reaps (a worker whose heartbeat stopped and whose
process is gone: its run was *interrupted*, and goes back in the queue to
be resumed) and spawns (queued runs, in order, one at a time per thread,
background ones only when the Mac has room). It holds no state of its own
— stop it at any moment, start it again, and it picks up from the
database. Workers it started keep running while it's gone.
"""
from __future__ import annotations

import fcntl
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Optional

from . import builds, db, machine, models, paths, quota, selfwork, store

log = logging.getLogger("eki.engine")

TICK = 0.5
#: a heartbeat older than this, with the process gone, is a dead worker
STALE = float(os.environ.get("EKI_STALE") or 10)
#: a heartbeat older than this, process or not, is a stuck worker
STUCK = 180
#: a worker that hasn't claimed its run in this long never will
STARTING = 30
#: interruptions in a row before a run is given up on
MAX_ATTEMPTS = 4
MAX_PARALLEL = int(os.environ.get("EKI_MAX_PARALLEL") or 8)


def alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_worker(pid: Optional[int], rid: str) -> bool:
    """Alive, and really the worker for this run — a pid can be reused."""
    if not alive(pid):
        return False
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True                      # can't tell: don't call it dead
    return f"eki.worker {rid}" in cmd


def reap(conn) -> None:
    now = db.now()
    for r in store.runs_in(conn, ("starting", "running")):
        if r["state"] == "starting":
            dead = (now - (r["spawned_at"] or now)) > STARTING and not is_worker(r["pid"], r["id"])
        else:
            beat = now - (r["heartbeat"] or 0)
            if beat <= STALE:
                continue
            mine = is_worker(r["pid"], r["id"])
            dead = not mine or beat > STUCK
            if dead and mine:
                try:
                    os.kill(r["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if dead:
            interrupted(conn, r)


def interrupted(conn, r) -> None:
    with db.tx(conn):
        cur = store.run(conn, r["id"])
        if cur["state"] not in ("starting", "running"):
            return
        store.add_event(conn, r["id"], cur["attempt"], "interrupted", {"pid": cur["pid"]})
        stop_child(cur["child_pid"])
        if cur["cancel"]:
            store.update_run(conn, r["id"], state="cancelled", ended_at=db.now())
        elif cur["attempt"] >= MAX_ATTEMPTS:
            store.update_run(conn, r["id"], state="failed", ended_at=db.now(),
                             error=f"interrupted {cur['attempt']} times")
        else:
            store.update_run(conn, r["id"], state="queued", pid=None)
    log.info("run %s interrupted; %s", r["id"], "queued to resume")


def stop_child(pid: Optional[int]) -> None:
    """The program a dead worker left behind: stop it before a resume starts another."""
    if not pid or not alive(pid):
        return
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def spawn_ready(conn) -> int:
    now = db.now()
    active = store.runs_in(conn, ("starting", "running"))
    busy_threads = {r["thread_id"] for r in active}
    # two runs in one folder would overwrite each other: they take turns
    busy_folders = {store.cwd_of(conn, r["thread_id"]) for r in active} - {None}
    slots = MAX_PARALLEL - len(active)
    started = 0
    queued = conn.execute("SELECT * FROM runs WHERE state='queued' ORDER BY "
                          "CASE priority WHEN 'now' THEN 0 ELSE 1 END, created_at").fetchall()
    room: Optional[tuple] = None
    for r in queued:
        if slots <= 0:
            break
        folder = store.cwd_of(conn, r["thread_id"])
        if r["thread_id"] in busy_threads or (r["retry_at"] and r["retry_at"] > now) \
                or folder in busy_folders:
            busy_threads.add(r["thread_id"])        # later runs of this thread wait their turn
            continue
        if r["priority"] == "background":
            room = room or machine.room()
            if not room[0]:
                continue
        busy_threads.add(r["thread_id"])
        if folder:
            busy_folders.add(folder)
        spawn(conn, r["id"])
        slots -= 1
        started += 1
    return started


def spawn(conn, rid: str) -> None:
    with db.tx(conn):
        cur = store.run(conn, rid)
        if cur["state"] != "queued":
            return
        store.update_run(conn, rid, state="starting", spawned_at=db.now(), pid=None,
                         build=builds.running_id())
    logf = open(paths.logs() / f"worker-{rid}.log", "ab")
    env = dict(os.environ)
    env.pop("EKI_PYTHON", None)            # the launcher's interpreter is its business, not a run's
    root = str(builds.running())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.Popen([sys.executable, "-m", "eki.worker", rid], cwd=str(paths.home()),
                            env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                            start_new_session=True, close_fds=True)
    logf.close()
    conn.execute("UPDATE runs SET pid=? WHERE id=? AND pid IS NULL", (proc.pid, rid))


#: how often the engine does its rounds of the local models
DUTY_EVERY = 15.0
_last_duty = [0.0]


_quota_job: list = [None, 0.0]


def refresh_quota() -> None:
    """Ask the subscription programs how much is left — on a thread of its own,
    since asking takes seconds and the engine's rounds must not wait."""
    job = _quota_job[0]
    if (job is not None and job.is_alive()) or time.time() - _quota_job[1] < 60:
        return
    _quota_job[1] = time.time()

    def work() -> None:
        c = db.connect()
        try:
            read = quota.refresh(c)
            if read:
                log.info("quota read: %s", ", ".join(read))
        except Exception:                                # noqa: BLE001
            log.exception("quota refresh failed")
        finally:
            c.close()

    _quota_job[0] = threading.Thread(target=work, daemon=True, name="quota")
    _quota_job[0].start()


_up_since = [0.0]
SELF_EVERY = 2.0
_last_self = [0.0]


def tick(conn) -> bool:
    """One round. False when this engine should step aside for a newer build."""
    reap(conn)
    spawn_ready(conn)
    refresh_quota()
    if time.time() - _last_self[0] > SELF_EVERY:
        _last_self[0] = time.time()
        try:
            for line in selfwork.tick(conn):
                log.info("self: %s", line)
        except Exception:                                # noqa: BLE001 — self-work must not stop the rest
            log.exception("self-work tick failed")
    if time.time() - _last_duty[0] > DUTY_EVERY:
        _last_duty[0] = time.time()
        for line in models.duty(conn):
            log.info(line)
        in_use = [r["build"] for r in store.runs_in(conn, ("starting", "running")) if r["build"]]
        for gone in builds.sweep(in_use):
            log.info("removed old build %s", gone)
    if _up_since[0] and time.time() - _up_since[0] > builds.WATCH:
        if builds.mark_healthy():
            log.info("build %s healthy after %ds", builds.running_id(), int(builds.WATCH))
    return not builds.step_aside()


def lock() -> Optional[IO[str]]:
    """The one-engine lock. Returns the held file, or None if another engine has it."""
    f = open(paths.engine_lock(), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def running_pid() -> Optional[int]:
    """The pid of the engine that holds the lock, if one does."""
    held = lock()
    if held is not None:
        held.close()
        return None
    try:
        return int(paths.engine_lock().read_text().strip() or 0) or None
    except (OSError, ValueError):
        return None


def serve() -> int:
    held = lock()
    if held is None:
        log.info("another engine is running")
        return 1
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)      # workers are reaped by the system
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    conn = db.connect()
    from . import server
    server.start_in_background()
    _up_since[0] = time.time()
    log.info("engine %s up, home %s, build %s", os.getpid(), paths.home(), builds.running_id())
    code = 0
    while not stopping:
        try:
            if not tick(conn):
                log.info("a newer build is current: stepping aside")
                code = builds.SWAP_EXIT
                break
        except Exception:                                # noqa: BLE001 — keep managing
            log.exception("tick failed")
        time.sleep(TICK)
    log.info("engine %s down; workers left running", os.getpid())
    held.close()
    return code


def start_detached() -> int:
    """Start an engine in the background (when launchd isn't looking after one)."""
    env = dict(os.environ)
    root = str(builds.running())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    logf = open(paths.logs() / "engine.log", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "eki.engine"], cwd=str(paths.home()), env=env,
                            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                            start_new_session=True, close_fds=True)
    logf.close()
    return proc.pid


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sys.exit(serve())
