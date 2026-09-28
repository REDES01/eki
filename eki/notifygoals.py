"""Goal-done notices: find the goals that ended and queue one push each.

A goal has no 'done' state; like standing._settle, its end is worked out
from the goals and items tables on every pass, and nothing is held in
memory. A planned goal has ended when it has a live item and none still
open; a failed goal has ended as it is. Only ends within the last day
count, so switching notify on never floods the phone with old goals, and a
restart inside a day loses none. notify.queue's key makes it one notice
per goal, however often this runs.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import db, notify

#: how far back an end still counts
WINDOW = 24 * 3600.0

OPEN = ("waiting", "building", "judging", "reviewing", "queued", "resolving", "rechecking", "locked")
FINISHED = ("live", "landed", "applied")

_IN_OPEN = ",".join("?" * len(OPEN))
_IN_FINISHED = ",".join("?" * len(FINISHED))

# the end: the latest of the goal's birth, its plan run's end and its items' last move
_END = ("MAX(g.created_at, COALESCE((SELECT r.ended_at FROM runs r WHERE r.id=g.plan_run), 0), "
        "COALESCE((SELECT MAX(i.updated_at) FROM items i WHERE i.goal_id=g.id), 0))")

_ENDED = (
    f"SELECT g.*, {_END} AS ended FROM goals g WHERE {_END} >= ? AND (g.state='failed' OR ("
    f"g.state='planned' AND EXISTS (SELECT 1 FROM items i WHERE i.goal_id=g.id AND i.state!='dropped') "
    f"AND NOT EXISTS (SELECT 1 FROM items i WHERE i.goal_id=g.id AND (i.state IN ({_IN_OPEN}) "
    f"OR (i.state='proposed' AND COALESCE(i.pr, '')='')))))"
)


def _finished(state: str, pr: Optional[str]) -> bool:
    return state in FINISHED or (state == "proposed" and bool(pr))


def title_of(conn: sqlite3.Connection, g: sqlite3.Row) -> str:
    items = conn.execute("SELECT state, pr FROM items WHERE goal_id=? AND state!='dropped'",
                         (g["id"],)).fetchall()
    if not items:
        return "eki · goal ended"
    left = sum(1 for i in items if not _finished(i["state"], i["pr"]))
    return f"eki · goal ended, {left} left" if left else "eki · goal done"


def body_of(g: sqlite3.Row) -> str:
    line = next((ln.strip() for ln in (g["text"] or "").splitlines() if ln.strip()), "")
    return (line or f"goal {g['id']}")[:120]


def click_of(conn: sqlite3.Connection, g: sqlite3.Row) -> Optional[str]:
    if not g["project"]:
        return None
    pr = conn.execute("SELECT pr FROM items WHERE goal_id=? AND COALESCE(pr, '')!='' "
                      "ORDER BY created_at, rowid LIMIT 1", (g["id"],)).fetchone()
    if pr:
        return pr["pr"]
    if g["issue"]:
        p = conn.execute("SELECT repo FROM projects WHERE id=?", (g["project"],)).fetchone()
        if p and p["repo"]:
            return f"https://github.com/{p['repo']}/issues/{int(g['issue'])}"
    return None


def tick(conn: sqlite3.Connection) -> List[str]:
    """Queue a notice for each goal that ended in the last day; one line per new one."""
    if not notify.on("goal_done"):
        return []
    said: List[str] = []
    ended = conn.execute(_ENDED, (db.now() - WINDOW, *OPEN)).fetchall()
    for g in ended:
        key = f"goal:{g['id']}:done"
        if conn.execute("SELECT 1 FROM notices WHERE key=?", (key,)).fetchone():
            continue                                     # queued already: skip the lookups
        if notify.queue(conn, key, "goal_done", title_of(conn, g), body_of(g), click_of(conn, g)):
            said.append(f"notify: goal {g['id']} done")
    return said
