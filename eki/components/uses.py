"""A component's uses, read from the runs table: nothing new is recorded.

A call is a run with provider 'component' whose prompt is the JSON `eki use`
writes ({"component", "args", "caller_dir", "caller_run"}). A call belongs to
a project when its caller_dir's `project_root` is that project's root.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .. import store
from . import project_root


def _call(prompt: str) -> Optional[dict]:
    try:
        c = json.loads(prompt or "")
    except (TypeError, ValueError):
        return None
    return c if isinstance(c, dict) and isinstance(c.get("component"), str) else None


def _rows(conn: sqlite3.Connection, name: str, root: Optional[Path]) -> List[sqlite3.Row]:
    """Call runs of `name` from `root` (None: from anywhere), newest first."""
    rows = conn.execute("SELECT * FROM runs WHERE provider='component' AND prompt LIKE ? "
                        "ORDER BY created_at DESC, rowid DESC", (f"%{name}%",)).fetchall()
    roots: Dict[str, Optional[Path]] = {}
    out = []
    for r in rows:
        c = _call(r["prompt"])
        if c is None or c["component"] != name:
            continue
        if root is not None:
            where = c.get("caller_dir") or ""
            if where not in roots:
                roots[where] = project_root(where, conn) if where else None
            if roots[where] != root:
                continue
        out.append(r)
    return out


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def calls(conn: sqlite3.Connection, name: str, root: Optional[Path], limit: int = 20) -> List[dict]:
    """The last `limit` calls of `name` from `root`, newest first, one dict each."""
    out = []
    for r in _rows(conn, name, root)[:limit]:
        start, end = r["started_at"], r["ended_at"]
        out.append({"run": r["id"], "when": r["created_at"], "started": start, "ended": end,
                    "caller": r["parent"] or "you", "state": r["state"],
                    "result": _first_line(store.answer(conn, r["id"])), "error": r["error"] or "",
                    "seconds": round(end - start, 1) if start is not None and end is not None else None})
    return out


def last_use(conn: sqlite3.Connection, name: str, root: Optional[Path]) -> Optional[float]:
    """When the newest call from `root` ended, else started, else was made; None when never."""
    rows = _rows(conn, name, root)
    if not rows:
        return None
    r = rows[0]
    return r["ended_at"] or r["started_at"] or r["created_at"]


def waiting(conn: sqlite3.Connection, name: str) -> int:
    """Calls of `name` still queued."""
    rows = conn.execute("SELECT prompt FROM runs WHERE provider='component' AND state='queued'").fetchall()
    return sum(1 for r in rows if (_call(r["prompt"]) or {}).get("component") == name)
