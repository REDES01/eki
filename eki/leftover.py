"""Background tasks a build left running in its worktree.

Build briefs tell the agent never to leave background jobs; when one does
anyway (a dev server, a watcher, a `sleep` it forgot), the process outlives
the run and holds the worktree. When the build is concluded, eki looks for
processes whose working directory is inside the item's worktree and ends
them (docs/self-build.md). Processes eki itself started for the item are
left alone (`ours`): servers recorded in ~/.eki/component-state/ and
everything in their process group or session, browser slot holders, and
eki's own CLI processes (card holds, `eki slot`, `python -m eki…`, any
`eki.` module) — only the agent's own background jobs are ended. On macOS the working directories come from
libproc (milliseconds, every process); elsewhere from `lsof`. No new dependency.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import subprocess
import time
from typing import Dict, Iterator, List, Set, Tuple

from . import paths

#: how long a SIGTERM gets before SIGKILL
GRACE = 2.0


def find(worktree: str) -> List[Dict]:
    """Processes whose working directory is the worktree or inside it:
    [{"pid", "cmd"}]. Never this process, the engine, or anything eki
    started itself (`ours`); [] on any error."""
    try:
        root = os.path.realpath(worktree)
        inside = [(pid, name) for pid, name, cwd in _cwds()
                  if cwd == root or cwd.startswith(root + os.sep)]
        if not inside:
            return []
        mine, groups = _own() | ours(), _groups()
        out = []
        for pid, name in inside:
            cmd = _command(pid)
            if pid in mine or _in(pid, groups) or _is_eki(cmd):
                continue
            out.append({"pid": pid, "cmd": _short(cmd) or name})
        return out
    except Exception:           # noqa: BLE001 — a look that fails finds nothing; judging goes on
        return []


def ours() -> Set[int]:
    """Pids eki started itself: the servers in component-state and the
    browser slot holders. A source that can't be read adds nothing."""
    out: Set[int] = set()
    try:
        from .components import servers
        for f in servers.state_dir().glob("*.json"):
            try:
                pid = int(json.loads(f.read_text()).get("pid") or 0)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            if pid > 0:
                out.add(pid)
    except Exception:           # noqa: BLE001 — spare nothing extra, never raise
        pass
    try:
        from . import browserslots
        out |= {p for p in browserslots.pids() if p > 0}
    except Exception:           # noqa: BLE001
        pass
    return out


def _groups() -> Set[Tuple[str, int]]:
    """The process groups and sessions of eki's servers: ("g"|"s", id)."""
    out: Set[Tuple[str, int]] = set()
    for pid in ours():
        for tag, get in (("g", os.getpgid), ("s", os.getsid)):
            try:
                out.add((tag, get(pid)))
            except OSError:
                pass
    return out


def _in(pid: int, groups: Set[Tuple[str, int]]) -> bool:
    for tag, get in (("g", os.getpgid), ("s", os.getsid)):
        try:
            if (tag, get(pid)) in groups:
                return True
        except OSError:
            pass
    return False


_MODULE = re.compile(r"^eki(\.\w+)*$")


def _is_eki(cmd: str) -> bool:
    """Does this command line run eki: the `eki` executable (also as a
    script under python), `python -m eki…`, or names an `eki.` module?"""
    args = cmd.split()
    if not args:
        return False
    if os.path.basename(args[0]) == "eki":
        return True
    if "python" in os.path.basename(args[0]) and len(args) > 1 and os.path.basename(args[1]) == "eki":
        return True
    return any((a == "-m" and i + 1 < len(args) and _MODULE.match(args[i + 1]))
               or (a.startswith("eki.") and _MODULE.match(a))
               for i, a in enumerate(args))


def _cwds() -> Iterator[Tuple[int, str, str]]:
    """(pid, short name, working directory) of every process we can see."""
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        yield from _lsof()
        return
    n = lib.proc_listallpids(None, 0)
    pids = (ctypes.c_int * (n * 2))()
    n = lib.proc_listallpids(pids, ctypes.sizeof(pids))
    buf = ctypes.create_string_buffer(_VNODEPATHINFO)
    for pid in pids[:max(n, 0)]:
        if lib.proc_pidinfo(pid, 9, ctypes.c_uint64(0), buf, _VNODEPATHINFO) == _VNODEPATHINFO:
            yield pid, "", buf.raw[152:152 + 1024].split(b"\0", 1)[0].decode(errors="replace")


#: struct proc_vnodepathinfo (PROC_PIDVNODEPATHINFO = 9): the cwd's vnode_info (152 bytes),
#: its path (MAXPATHLEN), then the root dir's, the same again
_VNODEPATHINFO = 2 * (152 + 1024)


def _lsof() -> Iterator[Tuple[int, str, str]]:
    out = subprocess.run(["lsof", "-w", "-d", "cwd", "-Fpcn"], capture_output=True,
                         text=True, timeout=20).stdout
    pid, name = 0, ""
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid, name = int(val) if val.isdigit() else 0, ""
        elif tag == "c":
            name = val
        elif tag == "n" and pid:
            yield pid, name, val


def kill(found: List[Dict]) -> List[Dict]:
    """SIGTERM each, SIGKILL whoever is still there after GRACE seconds.
    Returns the ones it signalled; never raises."""
    killed = []
    for p in found:
        try:
            os.kill(int(p["pid"]), signal.SIGTERM)
            killed.append(p)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    until = time.monotonic() + GRACE
    while killed and time.monotonic() < until and any(_alive(p["pid"]) for p in killed):
        time.sleep(0.05)
    for p in killed:
        if _alive(p["pid"]):
            try:
                os.kill(int(p["pid"]), signal.SIGKILL)
            except OSError:
                pass
    return killed


def _own() -> Set[int]:
    """This process, its parent, and the engine (from its lock file)."""
    mine = {os.getpid(), os.getppid()}
    try:
        mine.add(int(paths.engine_lock().read_text().strip() or 0))
    except (OSError, ValueError):
        pass
    return mine


def _alive(pid: int) -> bool:
    try:
        got, _ = os.waitpid(pid, os.WNOHANG)       # our own child: reap it, or it lingers as a zombie
        if got:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _command(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _short(cmd: str) -> str:
    return cmd if len(cmd) <= 60 else cmd[:59] + "…"
