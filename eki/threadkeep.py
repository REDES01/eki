"""The chat list's housekeeping, one step in housekeep.STEPS.

Each pass marks threads eki made for itself before `threads.by` existed,
purges deleted threads once nothing in them is active (eki/threadacts.py),
and settles and starts title chores for new chats (eki/titles.py). Every
step reads its work from the db, so a restart at any point loses nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import db, threadacts, titles

#: what makes a thread with no `by` one of eki's own (each clause on its own)
_EKI_OWN = """
    id IN (SELECT thread_id FROM chores WHERE thread_id IS NOT NULL)
 OR id IN (SELECT thread_id FROM runs WHERE row='chore' OR provider='command')
 OR id IN (SELECT thread_id FROM runs WHERE id IN (
        SELECT run_id FROM items WHERE run_id IS NOT NULL
        UNION SELECT gate2_run FROM items WHERE gate2_run IS NOT NULL
        UNION SELECT review_run FROM items WHERE review_run IS NOT NULL
        UNION SELECT draft_run FROM goals WHERE draft_run IS NOT NULL
        UNION SELECT plan_run FROM goals WHERE plan_run IS NOT NULL))
 OR title LIKE 'gate 2: %' OR title LIKE 'train check: build %'
"""


def backfill(conn: sqlite3.Connection) -> int:
    """Set by='eki' on old threads eki made for itself; the number marked.

    One UPDATE, so it is idempotent and a restart mid-way changes nothing."""
    with db.tx(conn):
        return conn.execute(f"UPDATE threads SET by='eki' WHERE by IS NULL AND ({_EKI_OWN})").rowcount


def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    marked = backfill(conn)
    if marked:
        said.append(f"threads: marked {marked} as eki's own")
    said.extend(threadacts.purge(conn))
    said.extend(titles.settle(conn))
    said.extend(titles.start_due(conn))
    return said
