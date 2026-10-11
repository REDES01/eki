"""A component's uses, read from the runs table: nothing new is recorded.

A call is a run with provider 'component' whose prompt is the JSON `eki use`
writes ({"component", "args", "caller_dir", "caller_run"}). A call belongs to
a project when its caller_dir's `project_root` is that project's root.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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


_WORKING = ("starting", "running")
_ACTIVE = ("queued", "starting", "running", "interrupted")


class _Folders:
    """Stands in for the connection `project_root` reads the projects table through, answering
    from one prefetched {path: folder}, so mapping many caller_dirs costs no query each."""

    def __init__(self, folders: Dict[str, str]):
        self.folders = folders

    def execute(self, _sql: str, params: tuple = ()) -> "_Folders":
        self.row = {"folder": self.folders[params[0]]} if params and params[0] in self.folders else None
        return self

    def fetchone(self) -> Optional[dict]:
        return self.row


def _held(r: sqlite3.Row) -> bool:
    """Whether this call makes its exclusive component held (access.holders, for one row)."""
    if r["started_at"] is None and r["state"] not in _WORKING:
        return False
    if r["parent"] is not None:
        return r["parent_state"] in _ACTIVE
    return r["state"] in _WORKING


def grid(conn: sqlite3.Connection, names: List[str], root: Optional[Path],
         specs: Optional[Dict[str, dict]] = None) -> Dict[str, Dict[str, Any]]:
    """{name: {"state", "last_use"}} for every name, in at most two SQL statements.

    state: "in use" (a call starting or running), "held" (exclusive and held), "busy" (its spec's
    provider has a run starting or running), "waiting" (a call queued), else "idle". last_use is
    `last_use()` for each name: the newest call from `root`'s ended, else started, else created."""
    specs = specs or {}
    wanted = set(names)
    rows = conn.execute("SELECT c.id, c.state, c.parent, c.prompt, c.created_at, c.started_at, c.ended_at, "
                        "p.state AS parent_state FROM runs c LEFT JOIN runs p ON p.id=c.parent "
                        "WHERE c.provider='component' ORDER BY c.created_at DESC, c.rowid DESC").fetchall()
    calls_of: Dict[str, List[Tuple[sqlite3.Row, dict]]] = {}
    for r in rows:
        c = _call(r["prompt"])
        if c is not None and c["component"] in wanted:
            calls_of.setdefault(c["component"], []).append((r, c))
    providers = sorted({p for n in names for p in [(specs.get(n) or {}).get("provider")]
                        if isinstance(p, str) and p})
    busy: Set[str] = set()
    folders: Dict[str, str] = {}
    parts, params = [], []  # type: List[str], List[Any]
    if providers:
        parts.append("SELECT 'busy' AS k, provider AS a, '' AS b FROM runs WHERE provider IN "
                     f"({','.join('?' * len(providers))}) AND state IN ('starting','running') GROUP BY provider")
        params += providers
    if root is not None and calls_of:
        parts.append("SELECT 'folder', path, folder FROM projects WHERE COALESCE(folder, '')!='' "
                     "AND COALESCE(state, 'on') NOT IN ('deleting', 'deleted')")
    if parts:
        for k, a, b in conn.execute(" UNION ALL ".join(parts), params).fetchall():
            if k == "busy":
                busy.add(a)
            else:
                folders[a] = b
    shim = _Folders(folders)
    roots: Dict[str, Optional[Path]] = {}
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        mine = calls_of.get(name, [])
        spec = specs.get(name) or {}
        newest = None
        for r, c in mine:
            if root is not None:
                where = c.get("caller_dir") or ""
                if where not in roots:
                    roots[where] = project_root(where, shim) if where else None  # type: ignore[arg-type]
                if roots[where] != root:
                    continue
            newest = r
            break
        if any(r["state"] in _WORKING for r, _ in mine):
            state = "in use"
        elif spec.get("access") == "exclusive" and any(_held(r) for r, _ in mine):
            state = "held"
        elif spec.get("provider") in busy:
            state = "busy"
        elif any(r["state"] == "queued" for r, _ in mine):
            state = "waiting"
        else:
            state = "idle"
        last = (newest["ended_at"] or newest["started_at"] or newest["created_at"]) if newest is not None else None
        out[name] = {"state": state, "last_use": last}
    return out
