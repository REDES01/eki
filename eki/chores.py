"""Chores: text jobs eki gives itself — a review, a digest's prose, a
fault's first brief, a PR comment's triage.

Each is an ordinary run on row `chore`, in a thread of its own, pinned to
the provider and model its role picks (eki/roles.py: `reviewer` for the
review, `chores` for the rest; by default the local model), with a row in
`chores` saying what it is for. Nothing is held in memory: the caller finds
finished chores with `finished` and moves each on once with `close`, so a
restart at any moment loses nothing. A chore run never hands off
(eki/providers/local.py sends no handoff tool on this row, eki/worker.py
fails one that tries), and it reaches Claude or Codex only when you set its
role there — and then as one call with no tools.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import roles, store
from .db import now

ROW = "chore"
KINDS = ("review", "digest", "brief")


def start(conn: sqlite3.Connection, kind: str, subject: str, prompt: str,
          priority: str, role: str = "chores") -> Optional[str]:
    """A chore's run on whoever does `role` now (eki/roles.py), or None — a
    `skipped` chore with the pick's why — when nobody can (or the role is off).

    It writes without a transaction of its own: the caller holds `db.tx`, so
    the chore and whatever it is for change together."""
    cid = store.new_id()
    pick = roles.pick(role, conn, background=priority == "background")
    if pick.provider is None:
        why = f"the {role} role is off" if pick.target == "off" else pick.why or f"no {role} model"
        conn.execute("INSERT INTO chores(id, kind, subject, state, result, created_at, ended_at)"
                     " VALUES (?,?,?,'skipped',?,?,?)",
                     (cid, kind, subject, why, now(), now()))
        return None
    tid = store.create_thread(conn, f"chore: {kind} {subject}", None, by="eki")
    rid = store.create_run(conn, tid, prompt, provider=pick.provider, model=pick.model,
                           effort=pick.effort, priority=priority, row=ROW)
    why = f"{role} → {roles.shown(pick.target)}"
    if pick.why:                               # a fallback names the role itself; a note doesn't
        why = pick.why if pick.why.startswith(f"{role} ") else f"{why}; {pick.why}"
    conn.execute("UPDATE runs SET why=? WHERE id=?", (why, rid))
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
