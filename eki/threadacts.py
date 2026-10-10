"""What you do to a chat from the list: pin, unread, rename, move, group, archive, delete.

Every write is one db.tx; a restart between two calls leaves a consistent row.
Delete hides a thread at once (deleted_at) and asks its active runs to stop;
`purge` removes the rows once nothing in it runs. Files, worktrees, branches
and attachments are never touched.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import db, projectchats, store

EKI_OWN = "eki's own thread: drop its work from the Station"


def _live(conn: sqlite3.Connection, tid: str) -> sqlite3.Row:
    t = store.thread(conn, tid) if tid else None
    if t is None or t["deleted_at"] is not None:
        raise KeyError(f"no thread {tid}")
    return t


def _title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        raise ValueError("a title can't be empty")
    return title[:80]


def _project_name(conn: sqlite3.Connection, pid: str) -> str:
    if pid == projectchats.SELF:
        return "eki"
    row = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    return row["name"] if row else pid


def _move(conn: sqlite3.Connection, t: sqlite3.Row, project: Optional[str]) -> None:
    """Set the thread's project; a new folder drops its sessions (a fresh start with the
    whole transcript, as after a handoff). None clears the project, keeping cwd and sessions."""
    if project is None:
        conn.execute("UPDATE threads SET project=NULL WHERE id=?", (t["id"],))
        return
    try:
        pid = projectchats.find(conn, str(project))
    except KeyError:
        raise ValueError(f"no project {project}") from None
    folder = projectchats.folder_of(conn, pid)
    if not folder:
        raise ValueError(f"{_project_name(conn, pid)} has no folder yet: set one on its page first")
    same = bool(t["cwd"]) and projectchats.norm(t["cwd"]) == folder
    conn.execute("UPDATE threads SET project=?, cwd=? WHERE id=?", (pid, folder, t["id"]))
    if not same:
        conn.execute("DELETE FROM sessions WHERE thread_id=?", (t["id"],))


def update(conn: sqlite3.Connection, tid: str, body: Dict[str, Any]) -> None:
    """Apply any of pin, unread, title, project, group, archive. KeyError for an unknown
    thread or group; ValueError for an empty title or a project with no folder."""
    with db.tx(conn):
        t = _live(conn, tid)
        tid = t["id"]
        now = db.now()
        if "pin" in body:
            conn.execute("UPDATE threads SET pinned_at=? WHERE id=?",
                         (now if body["pin"] else None, tid))
        if "unread" in body:
            if body["unread"]:
                conn.execute("UPDATE threads SET unread=1 WHERE id=?", (tid,))
            else:
                conn.execute("UPDATE threads SET unread=0, seen_at=? WHERE id=?", (now, tid))
        if "title" in body:
            conn.execute("UPDATE threads SET title=?, title_by='you' WHERE id=?",
                         (_title(body["title"]), tid))
        if "project" in body:
            _move(conn, t, body["project"])
        if "group" in body:
            gid = body["group"]
            if gid is not None and not conn.execute(
                    "SELECT 1 FROM chat_groups WHERE id=?", (gid,)).fetchone():
                raise KeyError(f"no group {gid}")
            conn.execute("UPDATE threads SET chat_group=? WHERE id=?", (gid, tid))
        if "archive" in body:
            conn.execute("UPDATE threads SET archived_at=? WHERE id=?",
                         (now if body["archive"] else None, tid))


def create_group(conn: sqlite3.Connection, name: str) -> Dict[str, str]:
    name = str(name or "").strip()
    if not name:
        raise ValueError("a group needs a name")
    gid = store.new_id()
    with db.tx(conn):
        top = conn.execute("SELECT MAX(ord) FROM chat_groups").fetchone()[0]
        conn.execute("INSERT INTO chat_groups(id, name, ord, created_at) VALUES (?,?,?,?)",
                     (gid, name, (top or 0) + 1, db.now()))
    return {"id": gid, "name": name}


def delete(conn: sqlite3.Connection, tid: str) -> int:
    """Hide the thread now and ask its active runs to stop; the number asked.
    The rows go in `purge`, once nothing in the thread is active."""
    marks = ",".join("?" * len(store.ACTIVE))
    with db.tx(conn):
        t = _live(conn, tid)
        if t["by"] == "eki":
            raise ValueError(EKI_OWN)
        tid = t["id"]
        conn.execute("UPDATE threads SET deleted_at=? WHERE id=?", (db.now(), tid))
        active = [r["id"] for r in conn.execute(
            f"SELECT id FROM runs WHERE thread_id=? AND state IN ({marks})", (tid, *store.ACTIVE))]
    for rid in active:
        store.cancel(conn, rid)
    purge(conn)
    return len(active)


def purge(conn: sqlite3.Connection) -> List[str]:
    """Remove the events, runs, asks, sessions and row of every deleted thread
    with no active run; one line each. Files are never touched."""
    marks = ",".join("?" * len(store.ACTIVE))
    said: List[str] = []
    with db.tx(conn):
        gone = conn.execute(
            f"SELECT id, title FROM threads WHERE deleted_at IS NOT NULL AND id NOT IN "
            f"(SELECT thread_id FROM runs WHERE state IN ({marks}))", store.ACTIVE).fetchall()
        for t in gone:
            tid = t["id"]
            conn.execute("DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE thread_id=?)",
                         (tid,))
            conn.execute("DELETE FROM asks WHERE thread_id=?", (tid,))
            conn.execute("DELETE FROM runs WHERE thread_id=?", (tid,))
            conn.execute("DELETE FROM sessions WHERE thread_id=?", (tid,))
            conn.execute("DELETE FROM threads WHERE id=?", (tid,))
            said.append(f"threads: removed deleted {tid} ({t['title'][:40]})")
    return said
