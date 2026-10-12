"""Card calls that finished after their build turn stopped waiting (docs/self-build.md, "Items").

A turn woken after the cap leaves its calls running; when one of them ends `done` later, nothing
read it. `unread` finds such calls for an item and `brief_lines` hands them to the item's next build
try, so it writes from the session instead of playing again. Everything is in the runs table.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import callwait, store


def unread(conn: sqlite3.Connection, it: sqlite3.Row) -> List[sqlite3.Row]:
    """The `done` component runs started by a run on the item's thread whose id no prompt on that
    thread made after the call ended carries (a wake message or an earlier brief from here)."""
    runs = store.thread_runs(conn, it["thread_id"])
    if not runs:
        return []
    marks = ",".join("?" * len(runs))
    calls = conn.execute(f"SELECT * FROM runs WHERE provider='component' AND state='done' AND parent IN ({marks})"
                         " ORDER BY created_at", [r["id"] for r in runs]).fetchall()
    out = []
    for c in calls:
        since = c["ended_at"] or c["created_at"]
        if not any(c["id"] in (r["prompt"] or "") for r in runs if r["created_at"] >= since):
            out.append(c)
    return out


def brief_lines(conn: sqlite3.Connection, calls: List[sqlite3.Row]) -> str:
    """The brief's paragraph naming each call and its session folder; '' for none."""
    lines = []
    for c in calls:
        paths = callwait._paths(conn, c["id"])
        folder = paths[0] if paths else f"in `eki show {c['id']}`"
        lines.append(f"`{callwait.card(c)}` run `{c['id']}` from an earlier try finished after that turn stopped"
                     f" waiting. Its session is `{folder}` (review.md, notes.md, screenshots/). Write the page"
                     " from it; do not play again.")
    return "\n\n" + "\n".join(lines) + "\n" if lines else ""
