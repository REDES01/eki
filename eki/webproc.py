"""The web server as the engine's child: `python -m eki.server`, started, kept and stopped.

The UI and API run in a process of their own so a request never waits behind
the engine's threads for the GIL. The child's stdin is a pipe the engine never
writes to: when the engine goes, by any means, the pipe closes and the server
exits (server._lifeline), so it never holds the port against the next engine.
With EKI_PORT=0 (the drill, candidates, tests) nothing is started.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from typing import Optional

from . import builds, observe, paths

log = logging.getLogger("eki.webproc")

#: the least time between two starts, so a server that dies at once isn't respawned in a spin
RESTART_PAUSE = 2.0

_last_start = [0.0]


def _wanted() -> bool:
    return os.environ.get("EKI_PORT") != "0"


def start() -> Optional[subprocess.Popen]:
    if not _wanted():
        return None
    _last_start[0] = time.monotonic()
    env = dict(os.environ)
    root = str(builds.running())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    logf = open(paths.logs() / "web.log", "ab")
    try:
        proc = subprocess.Popen([sys.executable, "-m", "eki.server"], cwd=str(paths.home()), env=env,
                                stdin=subprocess.PIPE, stdout=logf, stderr=logf,
                                start_new_session=True, close_fds=True)
    finally:
        logf.close()
    log.info("web server %s started", proc.pid)
    return proc


def keep(proc: Optional[subprocess.Popen]) -> Optional[subprocess.Popen]:
    """Called every engine loop: a server that has exited is written down and
    started again, at most once per RESTART_PAUSE."""
    if proc is not None:
        code = proc.poll()
        if code is None:
            return proc
        if getattr(proc, "_eki_noted", False) is False:
            log.warning("web server %s exited %s", proc.pid, code)
            observe.fault(None, f"web server exited {code}", "")
            proc._eki_noted = True                 # type: ignore[attr-defined]
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
    elif not _wanted():
        return None
    if time.monotonic() - _last_start[0] < RESTART_PAUSE:
        return proc
    try:
        return start()
    except OSError:
        log.exception("web server could not be started")
        observe.fault(None, "webproc.start")
        _last_start[0] = time.monotonic()
        return proc


def stop(proc: Optional[subprocess.Popen]) -> None:
    """Close the lifeline, TERM, wait up to 3 s, then KILL. Never raises."""
    if proc is None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    except Exception:                              # noqa: BLE001 — the engine is on its way out
        log.exception("web server %s: stop", getattr(proc, "pid", None))
