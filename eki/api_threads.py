"""The chat list: your chats, eki's threads only when they need you (or with `all`),
pinned first, archived apart. Deleted threads never show."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import projectchats, providers

#: the programs whose own session ids a person can copy and resume by hand
SESSION_KINDS = {"claude_code": "Claude Code", "codex": "Codex"}

#: eki's own threads by the title it gives them, for rows not yet marked by='eki'
#: (made before the creation sites pass `by`, and before threadkeep's backfill reached them)
_TITLED = "(" + " OR ".join(f"t.title LIKE '{p}%'" for p in
                            ("chore:", "self:", "gate 2:", "train check:", "plan:", "draft:")) + ")"
_EKI = f"(t.by = 'eki' OR (t.by IS NULL AND {_TITLED})) "
_MINE = f"(t.by IS NULL AND NOT {_TITLED}) "

_SELECT = (
    "SELECT t.*, MAX(r.created_at) AS updated, MAX(r.ended_at) AS last_ended, "
    " SUM(CASE WHEN r.state IN ('queued','starting','running') THEN 1 ELSE 0 END) AS active, "
    " (SELECT COUNT(*) FROM asks a WHERE a.thread_id = t.id AND a.state = 'open') AS waiting "
    "FROM threads t LEFT JOIN runs r ON r.thread_id = t.id "
    "WHERE t.deleted_at IS NULL {where} GROUP BY t.id ")
_ORDER = "ORDER BY waiting > 0 DESC, active > 0 DESC, COALESCE(updated, t.created_at) DESC"


def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = (), order: str = _ORDER) -> List[sqlite3.Row]:
    return conn.execute(_SELECT.format(where=where) + order, args).fetchall()


def threads(conn: sqlite3.Connection, limit: int = 60, all: bool = False,
            archived: bool = False) -> List[Dict[str, Any]]:
    """eki's threads with an open ask, then your pinned chats (always), then the rest
    up to `limit`. `all` adds eki's own threads; `archived` lists only archived ones."""
    mine = "" if all else "AND " + _MINE
    if archived:
        rows = _rows(conn, "AND t.archived_at IS NOT NULL " + mine,
                     order="ORDER BY COALESCE(updated, t.created_at) DESC")[:limit]
        return _entries(conn, rows)
    asking = [r for r in _rows(conn, "AND " + _EKI + "AND t.archived_at IS NULL ") if r["waiting"]]
    pinned = _rows(conn, "AND " + _MINE + "AND t.archived_at IS NULL AND t.pinned_at IS NOT NULL ",
                   order="ORDER BY t.pinned_at DESC")
    taken = {r["id"] for r in asking} | {r["id"] for r in pinned}
    rest = [r for r in _rows(conn, "AND t.archived_at IS NULL " + mine) if r["id"] not in taken]
    return _entries(conn, asking + pinned + rest[:max(0, limit - len(asking) - len(pinned))])


def entry(conn: sqlite3.Connection, tid: str) -> Dict[str, Any]:
    """One thread's list entry; KeyError for an unknown or deleted thread."""
    rows = _rows(conn, "AND t.id = ? ", (tid,))
    if not rows:
        raise KeyError(f"no thread {tid}")
    return _entries(conn, rows)[0]


def groups(conn: sqlite3.Connection) -> List[Dict[str, str]]:
    return [{"id": g["id"], "name": g["name"]}
            for g in conn.execute("SELECT id, name FROM chat_groups ORDER BY ord, created_at")]


def project(conn: sqlite3.Connection, pid: Optional[str]) -> Optional[Dict[str, str]]:
    """A thread's project as {id, name}, or None for a chat outside any project."""
    if not pid:
        return None
    if pid == projectchats.SELF:
        return {"id": pid, "name": "eki"}
    row = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    return {"id": pid, "name": row["name"] if row else pid}


def unread(t: sqlite3.Row) -> bool:
    """Marked by hand, or yours with a run that ended after you last looked.
    A NULL seen_at (a thread from before seen_at existed) reads as seen."""
    if t["unread"]:
        return True
    return (t["by"] is None and t["seen_at"] is not None and t["last_ended"] is not None
            and t["last_ended"] > t["seen_at"])


def _entries(conn: sqlite3.Connection, rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    kinds = {name: c.get("kind") for name, c in providers.config().items()}
    named = {g["id"]: g for g in groups(conn)}
    return [_entry(conn, t, kinds, named) for t in rows]


def _entry(conn: sqlite3.Connection, t: sqlite3.Row, kinds: Dict[str, Any],
           named: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    return {"id": t["id"], "title": t["title"], "provider": t["provider"], "cwd": t["cwd"],
            "updated": t["updated"] or t["created_at"], "working": bool(t["active"]),
            "needs_you": bool(t["waiting"]), "project": project(conn, t["project"]),
            "pinned": t["pinned_at"] is not None, "unread": unread(t), "own": t["by"] == "eki",
            "title_by": t["title_by"], "group": named.get(t["chat_group"]) if t["chat_group"] else None,
            "archived": t["archived_at"] is not None, "sessions": _sessions(conn, t["id"], kinds)}


def _sessions(conn: sqlite3.Connection, tid: str, kinds: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    for s in conn.execute("SELECT provider, session_id FROM sessions WHERE thread_id=? ORDER BY provider",
                          (tid,)):
        kind = kinds.get(s["provider"])
        if kind in SESSION_KINDS:
            out.append({"kind": kind, "label": SESSION_KINDS[kind], "id": s["session_id"]})
    return out
