"""Chores: jobs eki gives the local model — a review, a digest's prose, a
fault's first brief.

Each is an ordinary run, pinned to the local model on row `chore`, in a
thread of its own, with a row in `chores` saying what it is for. Nothing
is held in memory: the caller finds finished chores with `finished` and
moves each on once with `close`, so a restart at any moment loses nothing.
A chore run never hands off (eki/providers/local.py sends no handoff tool
on this row, eki/worker.py fails one that tries), so it never reaches Claude.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from . import paths, providers, store
from .db import now

ROW = "chore"
KINDS = ("review", "digest", "brief")


def local() -> Optional[str]:
    """The local model chores go to: routing.json `self.local`, else the first
    provider of kind `local`. None when it's "off" or there is none."""
    try:
        chosen = (json.loads(paths.config("routing").read_text()).get("self") or {}).get("local")
    except (OSError, ValueError, AttributeError):
        chosen = None
    cfg = providers.config()
    if chosen:
        return chosen if chosen != "off" and chosen in cfg else None
    return next((n for n, c in cfg.items() if c.get("kind") == "local"), None)


def start(conn: sqlite3.Connection, kind: str, subject: str, prompt: str,
          priority: str) -> Optional[str]:
    """A chore's run, or None (a `skipped` chore) with no local model.

    It writes without a transaction of its own: the caller holds `db.tx`, so
    the chore and whatever it is for change together."""
    cid, name = store.new_id(), local()
    if name is None:
        conn.execute("INSERT INTO chores(id, kind, subject, state, result, created_at, ended_at)"
                     " VALUES (?,?,?,'skipped','no local model',?,?)",
                     (cid, kind, subject, now(), now()))
        return None
    tid = store.create_thread(conn, f"chore: {kind} {subject}", None)
    rid = store.create_run(conn, tid, prompt, provider=name, priority=priority, row=ROW)
    conn.execute("INSERT INTO chores(id, kind, subject, run_id, thread_id, created_at)"
                 " VALUES (?,?,?,?,?,?)", (cid, kind, subject, rid, tid, now()))
    return rid


def finished(conn: sqlite3.Connection, kind: str) -> List[sqlite3.Row]:
    """Open chores of this kind whose run has ended (or is gone), with the run's
    `run_state` and `run_error`. Move each on with `close`."""
    marks = ",".join("?" * len(store.ACTIVE))
    return conn.execute(
        "SELECT c.*, r.state AS run_state, r.error AS run_error FROM chores c"
        " LEFT JOIN runs r ON r.id = c.run_id"
        f" WHERE c.kind=? AND c.state='open' AND (r.state IS NULL OR r.state NOT IN ({marks}))"
        " ORDER BY c.created_at", (kind, *store.ACTIVE)).fetchall()


def close(conn: sqlite3.Connection, chore_id: str, state: str, result: Optional[str] = None) -> bool:
    """Move an open chore to `state`; True only the one time it moves."""
    cur = conn.execute("UPDATE chores SET state=?, result=?, ended_at=? WHERE id=? AND state='open'",
                       (state, result, now(), chore_id))
    return cur.rowcount == 1


def latest(conn: sqlite3.Connection, kind: str, subject: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM chores WHERE kind=? AND subject=?"
                        " ORDER BY created_at DESC, rowid DESC LIMIT 1", (kind, subject)).fetchone()
