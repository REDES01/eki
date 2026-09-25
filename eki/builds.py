"""Which eki runs, and how a new one takes over (docs/self-build.md).

    ~/.eki/builds/
        a1b2c3d4e5f6/      an immutable export of one commit
        f6e5d4c3b2a1/
        current  -> f6e5d4c3b2a1        what the launcher starts
        previous -> a1b2c3d4e5f6        where it goes back to

The engine and every worker run from the folder they were started in and
never notice a swap; a worker started from an old build finishes there.
`swap_to` moves the symlinks and the engine steps aside at its next tick
(`step_aside`); the launcher (`bin/eki-launcher`, installed with the login
agent) starts `current` again, and flips back to `previous` if that engine
dies before it has written its healthy marker. In dev mode `current` is a
checkout, not a build, and editing it works as before.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

#: the exit code that tells the launcher "start current again"
SWAP_EXIT = 75
#: seconds an engine has to stay up before its build counts as healthy
WATCH = float(os.environ.get("EKI_WATCH") or 180)
KEEP_DAYS = 7


def root() -> Path:
    p = paths.home() / "builds"
    p.mkdir(exist_ok=True)
    return p


def source() -> Path:
    """The checkout this code came from (a build remembers its source; the
    launcher and the tests say with EKI_SOURCE)."""
    if os.environ.get("EKI_SOURCE"):
        return Path(os.environ["EKI_SOURCE"]).expanduser().resolve()
    here = Path(__file__).resolve().parent.parent
    info = here / ".eki-build.json"
    if info.exists():
        try:
            return Path(json.loads(info.read_text())["source"])
        except (ValueError, KeyError):
            pass
    return here


def running() -> Path:
    """The folder this process runs from: a build, or a checkout in dev mode."""
    return Path(os.environ.get("EKI_BUILD_DIR") or Path(__file__).resolve().parent.parent)


def running_id() -> str:
    info = running() / ".eki-build.json"
    if info.exists():
        try:
            return str(json.loads(info.read_text())["id"])
        except (ValueError, KeyError):
            pass
    return "dev"


def _link(name: str) -> Optional[Path]:
    p = root() / name
    return Path(os.readlink(p)) if p.is_symlink() else None


def current() -> Optional[Path]:
    return _link("current")


def previous() -> Optional[Path]:
    return _link("previous")


def _point(name: str, target: Path) -> None:
    link = root() / name
    tmp = root() / f".{name}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(str(target), tmp)
    os.replace(tmp, link)                       # one atomic step: never a missing link


def make(repo: str | Path, ref: Optional[str] = "HEAD") -> Path:
    """An export of `ref` of `repo` under builds/<id>. `ref=None` copies the
    working tree as it is (uncommitted edits included — for the drill)."""
    repo = Path(repo).resolve()
    if ref:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"],
                             capture_output=True, text=True, check=True).stdout.strip()
        bid = sha[:12]
    else:
        sha, bid = "worktree", f"wt-{time.time_ns() // 1000000}"
    dest = root() / bid
    if (dest / ".eki-build.json").exists():
        return dest
    tmp = root() / f".{bid}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    if ref:
        archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha],
                                 capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive, check=True)
    else:
        shutil.copytree(repo, tmp, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
    (tmp / ".eki-build.json").write_text(json.dumps(
        {"id": bid, "commit": sha, "source": str(repo), "made_at": time.time()}, indent=2))
    os.replace(tmp, dest)
    return dest


def swap_to(target: Path, why: str = "") -> Dict[str, Any]:
    """Point `current` at `target`; the engine steps aside at its next tick."""
    target = Path(target).resolve()
    if not (target / "eki" / "engine.py").exists():
        raise ValueError(f"{target} isn't an eki build or checkout")
    was = current()
    if was and was.resolve() == target:
        return {"state": "already", "target": str(target)}
    if was:
        _point("previous", was)
    _point("current", target)
    record = {"state": "swapping", "target": str(target), "previous": str(was) if was else None,
              "at": time.time(), "why": why}
    (root() / "swap.json").write_text(json.dumps(record, indent=2))
    return record


def back() -> Dict[str, Any]:
    prev = previous()
    if prev is None:
        raise ValueError("no previous build to go back to")
    return swap_to(prev, "asked to go back")


def step_aside() -> bool:
    """Is a different build now `current`? Only under the launcher, which starts it."""
    if not os.environ.get("EKI_LAUNCHED"):
        return False
    cur = current()
    return cur is not None and cur.resolve() != running().resolve()


def mark_healthy() -> Optional[str]:
    """Called by the engine once it has been up for WATCH: this build is fit."""
    marker = running() / ".healthy"
    if marker.exists():
        return None
    try:
        marker.write_text(f"{time.time()}\n")
    except OSError:
        return None
    rec = root() / "swap.json"
    if rec.exists():
        try:
            data = json.loads(rec.read_text())
            if data.get("target") == str(running().resolve()) and data.get("state") == "swapping":
                data.update(state="healthy", healthy_at=time.time())
                rec.write_text(json.dumps(data, indent=2))
        except ValueError:
            pass
    return running_id()


def status() -> Dict[str, Any]:
    out: Dict[str, Any] = {"current": str(current()) if current() else None,
                           "previous": str(previous()) if previous() else None,
                           "builds": []}
    for d in sorted(root().iterdir()):
        info = d / ".eki-build.json"
        if d.is_dir() and info.exists():
            data = json.loads(info.read_text())
            data["healthy"] = (d / ".healthy").exists()
            data["path"] = str(d)
            out["builds"].append(data)
    for name in ("swap.json", "rollback.json"):
        p = root() / name
        if p.exists():
            try:
                out[name[:-5]] = json.loads(p.read_text())
            except ValueError:
                pass
    return out


def sweep(in_use: List[str], days: float = KEEP_DAYS) -> List[str]:
    """Remove builds that are neither current nor previous, older than `days`,
    and not in `in_use` (build ids of runs still going)."""
    keep = {p.resolve() for p in (current(), previous()) if p}
    gone = []
    for d in root().iterdir():
        info = d / ".eki-build.json"
        if not (d.is_dir() and info.exists()) or d.resolve() in keep or d.name in in_use:
            continue
        if time.time() - info.stat().st_mtime > days * 86400:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone


def launcher_source() -> Path:
    return source() / "bin" / "eki-launcher"


def python() -> str:
    return os.environ.get("EKI_PYTHON") or sys.executable
