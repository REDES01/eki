"""workrun.HANDLERS for queue actions (merge, land; run_gate 2 and start_run resolve via START/GATES), and
`say`: how the queue's executors feed an event (docs/work-model.md).

An executor looks first (git, the runs), makes an Event of the facts and calls
`say`: it feeds the event, writes the executor's own columns (head, rebased,
gate2_run, landed_on …) in the same transaction, says on the board what
changed, and carries the actions out. start_run and run_gate are
eki/workhandlers_build.py's: it looks up the queue's roles and gates in START
and GATES here; the fixer, whatever stage called it, is its start_run(fix).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import db, workstep
from .workmodel import Action, Event, ItemState

Hook = Callable[[sqlite3.Connection, Optional[ItemState], ItemState], None]
#: an item's run starts say their own line (with the run id), not the state's
SAID_BY_HANDLER = ("start_run", "run_gate")


class _Moved(Exception):
    """The state moved between looking and feeding: nothing is written."""


# ---- feeding --------------------------------------------------------------------------------

def event(name: str, **data: Any) -> Event:
    return Event(name, db.now(), data)


def say(conn: sqlite3.Connection, gid: str, iid: Optional[str], ev: Event, then: Optional[Hook] = None,
        after: Optional[Hook] = None, carry: bool = True) -> List[str]:
    """Feed `ev` and return the board lines. `then(conn, old, new)` writes the executor's own
    columns of the event's item in feed's transaction, knowing what step decides (step is pure:
    the same state gives the same answer). `after(conn, old, new)` runs for each item that
    changed. `carry`: do the actions now (never from inside a handler)."""
    from . import workrun
    goal, items, settings = workrun.load(conn, gid)
    full = Event(ev.kind, ev.at, {**ev.data, "item": iid}) if iid is not None else ev
    _, new, _ = workstep.step(goal, tuple(items), settings, full)
    was = {x.id: x for x in items}
    mine = next((x for x in new if x.id == iid), None)

    def hook(c: sqlite3.Connection) -> None:
        if workrun.load(c, gid)[:2] != (goal, items):
            raise _Moved()
        if then is not None and mine is not None:
            then(c, was.get(iid), mine)

    try:
        actions = workrun.feed(conn, gid, iid, ev, then=hook)
    except _Moved:
        return []
    now = {x.id: x for x in workrun.load(conn, gid)[1]}
    lines: List[str] = []
    for x in new:
        if now.get(x.id) != x or was.get(x.id) == x:
            continue                                 # not written, or nothing changed
        if after is not None:
            after(conn, was.get(x.id), x)
        if x.stage == "gate 2" or any(a.item == x.id and a.kind in SAID_BY_HANDLER for a in actions):
            continue
        lines.append(f"item {x.id}: {x.why}")
    return lines + (workrun.carry(conn) if carry else [])


def stopped_error(conn: sqlite3.Connection, iid: str, label: str, error: str, **more: Any) -> None:
    """The `error` column in today's words once an item stopped (the board's why says more)."""
    from . import fixer, selfwork
    row = selfwork.store_item(conn, iid)
    if row is None or row["state"] != label:
        return
    tried = fixer.tried(row)
    with db.tx(conn):
        selfwork._set(conn, iid, error=f"{error} — {tried}" if tried else error, **more)


def can_fix(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Can a fixer run start for this item: the fixer on, its worktree and thread there."""
    from . import selfwork
    if row["fixer"] or not selfwork.settings().get("fixer", True):
        return False                                 # had its one fixer run
    return bool(row["worktree"]) and Path(row["worktree"]).is_dir() and bool(row["thread_id"])


# ---- the handlers ---------------------------------------------------------------------------

def _row(conn: sqlite3.Connection, iid: Optional[str]) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone() if iid else None


def _merge(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """Into the queue: at the back, its stack place made again by the next walk. A resolved
    one keeps the head and the commit its resolve left (eki/resolve.py)."""
    from . import selfwork
    fields: Dict[str, Any] = dict(queued_at=db.now(), gate2_run=None, gate2_on=None)
    if not action.data.get("back"):
        fields.update(head=None, rebased=None)
    with db.tx(conn):
        row = _row(conn, action.item)
        if row is not None and row["state"] == "queued":
            selfwork._set(conn, row["id"], **fields)
    return []


def _land(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """Land what is green at the front, on the target's tip: eki's integration main (the train
    takes it live), a project's branch through projectland."""
    if project.kind == "eki":
        from . import queue
        return queue.land(conn, carry=False)
    from . import projectqueue, projects
    row = projects.get(conn, project.id)
    return projectqueue.land(conn, row, carry=False) if row is not None else []


def _gate2(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    from . import queuestack
    return queuestack.start_gate2(conn, project, action)


def _resolve(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    from . import resolve
    return resolve.begin(conn, project, action)


def left_error(conn: sqlite3.Connection, old: Optional[ItemState], new: ItemState) -> None:
    """A left item's error in today's words: why, and what its fixer tried."""
    if new.stage == "left":
        stopped_error(conn, new.id, "left", new.why.split("left for you — ", 1)[-1])


HANDLERS: Dict[str, Callable] = {"merge": _merge, "land": _land}
#: the queue's roles and gates, looked up by eki/workhandlers_build.py's start_run and run_gate
#: (a resolve whose calls are back is woken there; a fix, gate 2's or resolve's, is its _fix)
START: Dict[str, Callable] = {"resolve": _resolve}
GATES: Dict[Any, Callable] = {2: _gate2}
