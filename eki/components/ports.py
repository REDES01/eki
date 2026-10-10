"""One port per served folder, recorded so it survives a restart of eki.

The record is `~/.eki/component-state/ports/allotted.json`:
{"folders": {resolved folder: port}, "freed": [ports]}. A folder keeps its
port while it is recorded; `free` gives it back (its worktree is gone), and a
freed port is offered first to the next new folder. Two folders never share
a port. A file lock keeps two writers (the engine, `eki use`) apart.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import socket
from pathlib import Path
from typing import Dict, Iterator, Optional

from .. import paths


def _dir() -> Path:
    d = paths.home() / "component-state" / "ports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file() -> Path:
    return _dir() / "allotted.json"


def norm(folder) -> str:
    return str(Path(folder).expanduser().resolve())


def _read() -> dict:
    try:
        got = json.loads(_file().read_text())
    except (OSError, ValueError):
        got = {}
    folders = got.get("folders") if isinstance(got, dict) else None
    freed = got.get("freed") if isinstance(got, dict) else None
    return {"folders": folders if isinstance(folders, dict) else {},
            "freed": freed if isinstance(freed, list) else []}


@contextlib.contextmanager
def _locked() -> Iterator[dict]:
    with open(_dir() / "lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            rec = _read()
            yield rec
            tmp = _file().with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, indent=1, sort_keys=True))
            tmp.replace(_file())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def bindable(port: int) -> bool:
    """Free to listen on, as the server binds it (SO_REUSEADDR: a stopped server's closing connections don't hold it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _fresh(taken: set) -> int:
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in taken:
            return port
    raise OSError("no free port on 127.0.0.1")


def get(folder) -> Optional[int]:
    """The port recorded for `folder`, or None."""
    p = _read()["folders"].get(norm(folder))
    return int(p) if p else None


def allot(folder) -> int:
    """The folder's port: the recorded one, else a freed one that is bindable, else a fresh one."""
    key = norm(folder)
    with _locked() as rec:
        if rec["folders"].get(key):
            return int(rec["folders"][key])
        taken = {int(p) for p in rec["folders"].values()}
        port = next((int(p) for p in rec["freed"] if int(p) not in taken and bindable(int(p))), 0)
        port = port or _fresh(taken)
        rec["freed"] = [p for p in rec["freed"] if int(p) != port]
        rec["folders"][key] = port
        return port


def free(folder) -> Optional[int]:
    """Forget the folder's port (offered again to a new folder); the port, or None."""
    key = norm(folder)
    with _locked() as rec:
        port = rec["folders"].pop(key, None)
        if port and int(port) not in rec["freed"]:
            rec["freed"].append(int(port))
        return int(port) if port else None


def allotted() -> Dict[str, int]:
    return {k: int(v) for k, v in _read()["folders"].items()}
