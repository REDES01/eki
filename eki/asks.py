"""What agents ask you while they work: questions, permissions, forms.

An ask is written down the moment the program asks, and waits for you in
the database — the web UI and `eki-next answer` both answer it here. The
worker that owns the run picks the answer up and hands it to the program.
If that worker dies, the ask is withdrawn and the resumed program asks
again, so an answer is never given to a question nobody is waiting on.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import store
from .db import dumps, now, tx


def create(conn: sqlite3.Connection, run_id: str, thread_id: str, kind: str,
           payload: Dict[str, Any]) -> str:
    aid = store.new_id()
    conn.execute("INSERT INTO asks(id, run_id, thread_id, kind, payload, created_at) VALUES (?,?,?,?,?,?)",
                 (aid, run_id, thread_id, kind, dumps(payload), now()))
    return aid


def view(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "run": row["run_id"], "thread": row["thread_id"], "kind": row["kind"],
            "state": row["state"], "created_at": row["created_at"],
            **json.loads(row["payload"]),
            "answer": json.loads(row["answer"]) if row["answer"] else None}


def get(conn: sqlite3.Connection, aid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM asks WHERE id=? OR id LIKE ?", (aid, aid + "%")).fetchone()


def open_asks(conn: sqlite3.Connection, thread_id: Optional[str] = None) -> List[sqlite3.Row]:
    if thread_id:
        return conn.execute("SELECT * FROM asks WHERE state='open' AND thread_id=? ORDER BY created_at",
                            (thread_id,)).fetchall()
    return conn.execute("SELECT * FROM asks WHERE state='open' ORDER BY created_at").fetchall()


def for_run(conn: sqlite3.Connection, run_id: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM asks WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()


def answer(conn: sqlite3.Connection, aid: str, response: Dict[str, Any]) -> str:
    """Your answer. Only an open ask takes one."""
    with tx(conn):
        row = get(conn, aid)
        if row is None:
            raise KeyError(f"no ask {aid}")
        if row["state"] != "open":
            return row["state"]
        conn.execute("UPDATE asks SET state='answered', answer=?, answered_at=? WHERE id=?",
                     (dumps(response), now(), row["id"]))
    return "answered"


def take_answers(conn: sqlite3.Connection, ids: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
    """For the worker: answers waiting to be handed to the program, marked handed."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    got = []
    with tx(conn):
        for row in conn.execute(f"SELECT * FROM asks WHERE state='answered' AND id IN ({marks})", ids):
            got.append((row["id"], json.loads(row["answer"])))
        if got:
            conn.execute(f"UPDATE asks SET state='delivered' WHERE id IN ({','.join('?' * len(got))})",
                         [g[0] for g in got])
    return got


def withdraw(conn: sqlite3.Connection, aid: str) -> None:
    conn.execute("UPDATE asks SET state='withdrawn' WHERE id=? AND state IN ('open','answered')", (aid,))


def withdraw_run(conn: sqlite3.Connection, run_id: str) -> None:
    """A run starting again: whatever its last attempt asked will be asked again."""
    conn.execute("UPDATE asks SET state='withdrawn' WHERE run_id=? AND state IN ('open','answered')",
                 (run_id,))
