"""The owner's ~/.claude/CLAUDE.md and eki's AGENTS.md kept as one text.

Each housekeeping pass compares the two. One missing is written from the
other (never creating ~/.claude). When both exist and differ, the one changed
last (by mtime; a tie goes to the owner's CLAUDE.md) wins: a unified diff
older -> newer goes to `sync/instructions.diff`, the older is copied to
`sync/backups/` and then overwritten atomically. Nothing is kept in memory —
the files and `sync/state.json` are the whole state.
"""
from __future__ import annotations

import difflib
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from . import syncstate

DIRECTION = "instructions"


def _files() -> List[Path]:
    return [syncstate.claude_dir() / "CLAUDE.md", syncstate.agents_md()]


def _text(path: Path) -> Optional[str]:
    try:
        return path.read_text() if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _write(path: Path, text: str) -> Path:
    """Atomic write through a symlink, so a linked file stays linked."""
    target = path.resolve()
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, target)
    return path


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = syncstate.backups()
    dest = base / f"{path.name}-{stamp}.md"
    n = 1
    while dest.exists():                       # two drifts in one second keep both backups
        dest = base / f"{path.name}-{stamp}-{n}.md"
        n += 1
    shutil.copy2(path, dest)
    return dest


def diff_path() -> Path:
    return syncstate.root() / "instructions.diff"


def diff() -> str:
    """The last drift's diff (older -> newer), or ''."""
    try:
        return diff_path().read_text()
    except OSError:
        return ""


def _last_clashes() -> List[str]:
    return list(syncstate.status()[DIRECTION].get("clashes") or [])


def tick(conn: sqlite3.Connection) -> List[str]:
    """One pass; returns a line per file written."""
    claude, agents = _files()
    a, b = _text(claude), _text(agents)
    said: List[str] = []
    clashes = _last_clashes()

    if a is None and b is None:
        syncstate.record(DIRECTION, count=0, clashes=clashes, note="none")
        return said

    if a is None or b is None:
        source, text, dest = (agents, b, claude) if a is None else (claude, a, agents)
        if dest.parent.is_dir():               # ~/.claude is the owner's; never created here
            _write(dest, text)
            said.append(f"instructions: {dest} written from {source}")
        syncstate.record(DIRECTION, count=1 if said else 0, clashes=clashes, note="one missing")
        return said

    if a == b:
        syncstate.record(DIRECTION, count=1, clashes=clashes, note="equal")
        return said

    if _mtime(agents) > _mtime(claude):
        newer, new_text, older, old_text = agents, b, claude, a
    else:
        newer, new_text, older, old_text = claude, a, agents, b
    patch = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=str(older), tofile=str(newer)))
    _write(diff_path(), patch)
    said.append(f"instructions: diff written to {diff_path()}")
    saved = _backup(older)
    said.append(f"instructions: {older} backed up to {saved}")
    _write(older, new_text)
    said.append(f"instructions: {older} replaced by {newer}")
    clash = f"drift: {newer.name} replaced {older.name}, backup {saved}"
    syncstate.record(DIRECTION, count=1, clashes=[clash], note="equal")
    return said
