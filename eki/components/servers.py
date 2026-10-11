"""Servers of `serve`-form service components: started on demand, stopped when idle.

Each server eki starts has a state file under ~/.eki/component-state/, keyed
by the component's name and folder (two projects' `voice` never collide), and
for a card with one server per served folder (the `serve` kind) by that too:
its pid, when it was started, and the run of its latest call. The server
runs detached in a session of its own, so a restart of eki leaves it up and
finds it again from the file. eki only stops servers it started.

Idle is read from the runs table: the end of the latest call run (or, while
that run is still active, not idle at all). `idle_stop` minutes (default 5;
0 never) after that, the housekeeping step `tick` stops it. A server for an
item's worktree stops as soon as the item is finished (applied, landed, live,
dropped) or its folder is gone, whatever idle_stop says.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths

log = logging.getLogger("eki.components.servers")

#: how long stop() gives a server to exit before it is killed
STOP_WAIT = 5.0
#: between health polls while a server comes up
POLL = 0.2
ACTIVE = ("queued", "starting", "running", "waiting")


def state_dir() -> Path:
    d = paths.home() / "component-state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def key(name: str, folder, served: str = "") -> str:
    where = str(Path(folder).resolve()) + (f"\0{served}" if served else "")
    h = hashlib.sha1(where.encode()).hexdigest()[:10]
    return f"{name}-{h}"


def _path(comp, served: str = "") -> Path:
    return state_dir() / f"{key(comp.name, comp.folder, served)}.json"


def read(comp, served: str = "") -> Dict[str, Any]:
    try:
        return json.loads(_path(comp, served).read_text())
    except (OSError, ValueError):
        return {}


def states() -> List[Dict[str, Any]]:
    """Every state file's content, with its own path as "state"."""
    out = []
    for f in sorted(state_dir().glob("*.json")):
        try:
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(st, dict):
            out.append(dict(st, state=str(f)))
    return out


def write(comp, served: str = "", **fields: Any) -> None:
    st = read(comp, served)
    st.update(fields, name=comp.name, folder=str(comp.folder))
    if served:
        st["served"] = served
    p = _path(comp, served)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(p)


def alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:      # ours and exited: reaped now
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def health_url(spec: dict) -> str:
    """`health` as a full URL, or as a path under `url`."""
    h = str(spec.get("health", ""))
    if "://" in h or not spec.get("url"):
        return h
    return str(spec["url"]).rstrip("/") + "/" + h.lstrip("/")


def healthy(spec: dict, timeout: float = 2.0) -> bool:
    url = health_url(spec)
    if "://" not in url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def is_up(comp) -> bool:
    return healthy(comp.spec, timeout=1.0)


def ours(comp, served: str = "") -> bool:
    return alive(read(comp, served).get("pid"))


def start(comp, by: str = "") -> Dict[str, Any]:
    """Start the server detached unless it (or ours) is already there; then wait for health."""
    spec = comp.spec
    if not healthy(spec) and not ours(comp):
        env = dict(os.environ)
        env.update({k: os.path.expanduser(str(v)) for k, v in (spec.get("env") or {}).items()})
        cwd = Path(os.path.expanduser(str(spec.get("cwd") or ".")))
        if not cwd.is_absolute():
            cwd = comp.folder / cwd
        logf = open(paths.logs() / f"component-{key(comp.name, comp.folder)}.log", "ab")
        try:
            proc = subprocess.Popen([os.path.expanduser(a) if a.startswith("~") else a for a in spec["serve"]],
                                    cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=logf,
                                    stderr=logf, start_new_session=True)
        finally:
            logf.close()
        write(comp, pid=proc.pid, started_at=time.time(), by=by)
        log.info("started component %s (pid %s) for %s", comp.name, proc.pid, by or "a call")
    end = time.time() + float(spec.get("start_timeout", 120))
    while time.time() < end:
        if healthy(spec):
            return {"up": True, "why": ""}
        st = read(comp)
        if st.get("pid") and not alive(st["pid"]):
            return {"up": False, "why": f"{comp.name}'s server exited while starting "
                                        f"(see the log in {paths.logs()})"}
        time.sleep(POLL)
    return {"up": False, "why": f"{comp.name} didn't answer {spec.get('health')} within "
                                f"{spec.get('start_timeout', 120)} s"}


def stop(comp, served: str = "") -> bool:
    """Stop the server if eki started it; False when there was nothing of ours to stop."""
    return stop_pid(read(comp, served).get("pid"), _path(comp, served), comp.name)


def stop_pid(pid: Optional[int], state: Path, name: str) -> bool:
    if not alive(pid):
        state.unlink(missing_ok=True)
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    end = time.time() + STOP_WAIT
    while alive(pid) and time.time() < end:
        time.sleep(0.05)
    if alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    state.unlink(missing_ok=True)
    log.info("stopped component %s (pid %s)", name, pid)
    return True


def used(comp, run_id: str, served: str = "") -> None:
    """A call run is using the server; its end is the server's last use."""
    write(comp, served, last_run=run_id, last_call_at=time.time())


def _last_use(conn: sqlite3.Connection, st: Dict[str, Any]) -> Optional[float]:
    """When the server was last used; None while its latest call run is still active."""
    t = max(float(st.get("started_at") or 0), float(st.get("last_call_at") or 0))
    rid = st.get("last_run")
    if rid:
        row = conn.execute("SELECT state, ended_at FROM runs WHERE id=?", (rid,)).fetchone()
        if row is not None:
            if row["state"] in ACTIVE:
                return None
            t = max(t, float(row["ended_at"] or 0))
    return t


#: an item in one of these is finished: its worktree's server has nothing left to show
FINISHED = ("applied", "landed", "live", "dropped")


def _finished(conn: sqlite3.Connection, served: str) -> Optional[str]:
    """Why a served folder needs no server any more (its item finished, or the folder is gone); None if it may."""
    if not served:
        return None
    if not os.path.isdir(served):
        return "its folder is gone"
    try:
        row = conn.execute("SELECT id, state FROM items WHERE worktree=? ORDER BY updated_at DESC LIMIT 1",
                           (served,)).fetchone()
    except sqlite3.Error:
        return None
    if row is not None and row[1] in FINISHED:
        return f"item {row[0]} is {row[1]}"
    return None


def tick(conn: sqlite3.Connection) -> List[str]:
    """Housekeeping: stop each server of ours whose served folder's item has finished (or whose
    folder is gone) at once, and any other idle for longer than its idle_stop minutes."""
    said: List[str] = []
    for f in sorted(state_dir().glob("*.json")):
        try:
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        pid = st.get("pid")
        if not alive(pid):
            f.unlink(missing_ok=True)
            continue
        done = _finished(conn, st.get("served") or "")
        if done:
            if stop_pid(pid, f, st.get("name", f.stem)):
                said.append(f"stopped component {st.get('name', f.stem)} for {Path(st['served']).name}: {done}")
            continue
        try:
            spec = json.loads((Path(st["folder"]) / "component.json").read_text())
        except (KeyError, OSError, ValueError):
            spec = {}
        minutes = float(spec.get("idle_stop", st.get("idle_stop", 5)))
        if minutes <= 0:
            continue
        last = _last_use(conn, st)
        if last is None or time.time() - last <= minutes * 60:
            continue
        if stop_pid(pid, f, st.get("name", f.stem)):
            said.append(f"stopped component {st.get('name', f.stem)}: idle for {minutes:g} min")
    return said
