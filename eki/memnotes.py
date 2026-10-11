"""Claude's memory, client -> eki: the mirror as notes every run can search.

The client task (a scheduled Claude session set up outside eki) mirrors the
owner's Claude memory into `~/.eki/sync/claude-memory/`. eki only reads it: a
run's brief gets a short index — one line per file, its path and description —
and the run reads a file on demand. No file body is ever pasted into a prompt.
A missing folder is no notes, not an error.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Dict, List

from . import syncstate

log = logging.getLogger("eki.memnotes")

#: a description line is cut to this
DESCRIPTION = 120
#: the whole brief block stays about this long
CAP = 4000


def _describe(path: Path) -> str:
    """The front matter's `description:`, else the first non-empty line, cut to DESCRIPTION chars."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for _, ln in zip(range(60), f)]
    except OSError:
        return ""
    body = lines
    if lines and lines[0] == "---":
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        if end is not None:
            for ln in lines[1:end]:
                key, _, value = ln.partition(":")
                if key.strip().lower() == "description" and value.strip():
                    return value.strip().strip("\"'")[:DESCRIPTION]
            body = lines[end + 1:]
    first = next((ln for ln in body if ln), "")
    return first.lstrip("#").strip()[:DESCRIPTION]


def index() -> List[Dict]:
    """Every .md file in the mirror, sorted by path: {'path': '/<relative>', 'description': ...}."""
    folder = syncstate.memory_dir()
    if not folder.is_dir():
        return []
    try:
        files = [p for p in folder.rglob("*.md") if p.is_file()]
    except OSError:
        return []
    found = [{"path": "/" + p.relative_to(folder).as_posix(), "description": _describe(p)} for p in files]
    return sorted(found, key=lambda e: e["path"])


def brief() -> str:
    """The index as a short block for a run's brief; '' when there are no notes."""
    notes = index()
    if not notes:
        return ""
    head = (f"The owner's notes from Claude (their Claude memory) are in {syncstate.memory_dir()}. "
            "Read a file only when it matters to this work:")
    lines = [head]
    size = len(head)
    for i, n in enumerate(notes):
        line = f"{n['path']} — {n['description']}" if n["description"] else n["path"]
        rest = len(notes) - i
        tail = len(f"\n… and {rest} more")
        if size + 1 + len(line) + (tail if rest > 1 else 0) > CAP:
            lines.append(f"… and {rest} more")
            break
        lines.append(line)
        size += 1 + len(line)
    return "\n".join(lines)


def tick(conn: sqlite3.Connection) -> List[str]:
    syncstate.record("memory-in", count=len(index()))
    return []
