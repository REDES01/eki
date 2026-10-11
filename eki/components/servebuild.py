"""A serve card's `build`: the shell command that makes a served folder servable.

A project compiled before it runs (TypeScript → slice/js/ by esbuild) names
its build on its serve card, e.g. "npm run build". serve.ensure runs it in
the served folder before it starts a server, and again whenever the folder's
sources changed since the last successful build.

"Changed" is cheap: the newest mtime among the folder's source files — what
git lists, tracked or not yet added, minus the ignored, so built output never
counts — against that same newest mtime when the last successful build
started (so a clock off, or a file dated ahead, can't keep it rebuilding). It is
kept per card and folder under ~/.eki/component-state/builds/, so it outlives
a restart. One build per folder at a time, across processes (a file lock).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .. import paths
from . import Component, ports, servers

#: how long a build may take before it counts as failed
TIMEOUT = 600.0
#: how many of the output's last lines an error carries
TAIL = 20


def command(comp: Component) -> str:
    c = comp.spec.get("build")
    return c.strip() if isinstance(c, str) else ""


def _dir() -> Path:
    d = servers.state_dir() / "builds"            # a subfolder: servers.states() reads only the top
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state(comp: Component, folder: str) -> Path:
    return _dir() / f"{servers.key(comp.name, comp.folder, folder)}.json"


def last(comp: Component, folder) -> dict:
    """The last successful build of `folder` for this card: {"sources_at", "started_at", "command"}; {} when none."""
    try:
        return json.loads(_state(comp, ports.norm(folder)).read_text())
    except (OSError, ValueError):
        return {}


def newest(folder) -> float:
    """The newest mtime among the folder's source files (git's view: tracked or untracked, not ignored)."""
    folder = ports.norm(folder)
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=folder, capture_output=True, timeout=30, check=True).stdout
        names = [n for n in out.decode(errors="replace").split("\0") if n]
    except (OSError, subprocess.SubprocessError):     # not in git: every file but .git's
        names = []
        for d, subs, files in os.walk(folder):
            subs[:] = [s for s in subs if s != ".git"]
            names += [os.path.relpath(os.path.join(d, f), folder) for f in files]
    best = 0.0
    for n in names:
        try:
            best = max(best, os.stat(os.path.join(folder, n)).st_mtime)
        except OSError:                           # listed but deleted: no file, no mtime
            pass
    return best


def changed(comp: Component, folder) -> bool:
    """The card builds, and `folder` changed (or the command did) since its last successful build."""
    cmd = command(comp)
    if not cmd:
        return False
    got = last(comp, folder)
    if not got or got.get("command") != cmd:
        return True
    return newest(folder) != got.get("sources_at")


def _tail(text: str) -> str:
    return "\n".join(text.rstrip().splitlines()[-TAIL:])


def build(comp: Component, folder) -> Optional[str]:
    """Run the card's build in `folder`. None when it succeeded (or there is none), else why, ending in the output tail."""
    cmd = command(comp)
    if not cmd:
        return None
    folder = ports.norm(folder)
    lock = _dir() / f"{hashlib.sha1(folder.encode()).hexdigest()[:10]}.lock"
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)            # one build per folder at a time, across processes
        try:
            started, sources_at = time.time(), newest(folder)
            try:
                p = subprocess.run(cmd, shell=True, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=TIMEOUT)
                out, rc = p.stdout.decode(errors="replace"), p.returncode
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
                rc = None
            except OSError as e:
                out, rc = str(e), -1
            log = paths.logs() / f"build-{servers.key(comp.name, comp.folder, folder)}.log"
            with open(log, "a") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {cmd} in {folder}: "
                        f"{'timed out' if rc is None else f'exit {rc}'}\n{out}\n")
            if rc == 0:
                st = _state(comp, folder)
                tmp = st.with_suffix(".tmp")
                tmp.write_text(json.dumps({"sources_at": sources_at, "started_at": started, "command": cmd, "folder": folder}))
                os.replace(tmp, st)
                return None
            how = f"timed out after {TIMEOUT:g} s" if rc is None else f"exit {rc}"
            return f"{comp.name} build failed ({how}): {_tail(out) or '(no output)'}"
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
