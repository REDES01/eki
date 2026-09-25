"""The engine: start, stop, status, and installing it as a login agent."""
from __future__ import annotations

import os
import signal
import sys
import time

from .. import engine, launchd, paths, store
from .common import conn

NAME = "engine"
HELP = "start, stop or check the engine; install it as a login agent"


def add(p) -> None:
    p.add_argument("action", choices=["status", "start", "stop", "restart", "run", "install",
                                      "uninstall", "log"], nargs="?", default="status")


def run(args) -> int:
    a = args.action
    pid = engine.running_pid()
    if a == "status":
        c = conn()
        active = store.runs_in(c, store.ACTIVE)
        where = "login agent" if launchd.installed() else "not installed as a login agent"
        print(f"engine: {'up, pid ' + str(pid) if pid else 'down'} ({where})")
        print(f"home:   {paths.home()}")
        print(f"runs:   {sum(r['state'] == 'running' for r in active)} running, "
              f"{sum(r['state'] == 'queued' for r in active)} queued")
        return 0
    if a == "run":
        import logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        return engine.serve()
    if a == "start":
        if pid:
            print(f"already up, pid {pid}")
        elif launchd.installed():
            launchd.kick()
            print("started (login agent)")
        else:
            print(f"started, pid {engine.start_detached()}")
        return 0
    if a in ("stop", "restart"):
        if launchd.installed():
            if a == "restart":
                launchd.kick()
                print("restarted; runs keep going")
                return 0
            print("the login agent restarts it; use `engine uninstall` to stop it for good")
            return 1
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if engine.running_pid() is None:
                    break
                time.sleep(0.1)
        if a == "restart":
            print(f"restarted, pid {engine.start_detached()}; runs keep going")
        else:
            print("stopped; runs keep going, queued ones wait for the next engine")
        return 0
    if a == "install":
        if pid and not launchd.installed():
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
        print(f"installed: {launchd.install()}")
        return 0
    if a == "uninstall":
        print("removed" if launchd.uninstall() else "wasn't installed")
        return 0
    if a == "log":
        log = paths.logs() / "engine.log"
        sys.stdout.write(log.read_text()[-4000:] if log.exists() else "no log yet\n")
        return 0
    return 1
