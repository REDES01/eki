"""The work model's executor core: save state, carry out actions
(docs/work-model.md).

`feed` is the one way a goal or item changes state: it loads the goal and
its items, and under one transaction rechecks they are as loaded (a person
or a restart may have moved them; then nothing is written), calls
workstep.step, writes the new states (`items.state` = label, `items.work`,
`goals.state`, `goals.work`) and inserts the actions into `work_actions`.
`carry` performs the actions not yet done through HANDLERS and marks each
done. The engine calls `carry` before the work ticks, so a restart between
saving an action and doing it does it then, once: every handler is
idempotent (a run start is keyed by item, stage and tries).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import db, notify, workhandlers_build, workhandlers_queue, workproject, workstep
from .workmodel import (GOAL_STAGES, ITEM_STAGES, Action, Event, GoalState, ItemState, Settings, goal_label,
                        label)

log = logging.getLogger("eki.workrun")

Loaded = Tuple[GoalState, List[ItemState], Settings]


# ---- state ----------------------------------------------------------------------------------

def load(conn: sqlite3.Connection, goal_id: str) -> Loaded:
    g = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if g is None:
        raise KeyError(f"no goal {goal_id}")
    rows = conn.execute("SELECT * FROM items WHERE goal_id=? ORDER BY created_at, id", (goal_id,)).fetchall()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (g["project"],)).fetchone() if g["project"] else None
    if g["project"] and row is None:
        raise KeyError(f"no project {g['project']}")
    return _goal(g), [_item(r) for r in rows], workproject.settings(conn, row)


def _json(text: Optional[str]) -> Any:
    try:
        return json.loads(text or "null")
    except ValueError:
        return None


def _goal(g: sqlite3.Row) -> GoalState:
    got = _json(g["work"])
    if isinstance(got, dict) and got.get("stage") == g["state"]:
        return GoalState(**got)
    return GoalState(phase=GOAL_STAGES.get(g["state"], "failed"), stage=g["state"], why=g["error"] or "")


def _item(r: sqlite3.Row) -> ItemState:
    """The item as saved; its row's own columns when `work` is missing or older than its state."""
    got = _json(r["work"])
    if isinstance(got, dict) and got.get("stage") in ITEM_STAGES and label(_from(got)) == r["state"]:
        return _from(got)
    return ItemState(id=r["id"], phase=ITEM_STAGES.get(r["state"], "stopped"), stage=r["state"],
                     deps=tuple(_json(r["deps"]) or ()), files=tuple(_json(r["files"]) or ()),
                     touched=tuple(_json(r["touched"]) or ()), tries=r["tries"] or 0, reviews=r["reviews"] or 0,
                     fixer_used=bool(r["fixer"]), gate2=r["gate2"], review=r["review"],
                     why=r["error"] or r["why"] or "")


def _from(got: Dict[str, Any]) -> ItemState:
    return ItemState(**{**got, **{k: tuple(got.get(k) or ()) for k in ("deps", "files", "touched")}})


# ---- feed -----------------------------------------------------------------------------------

def feed(conn: sqlite3.Connection, goal_id: str, item_id: Optional[str], event: Event,
         then: Optional[Callable[[sqlite3.Connection], None]] = None) -> List[Action]:
    """One event for the goal (and its item): the new state and actions, saved together.
    `then(conn)` runs in the same transaction before the writes, only when the recheck held
    (an executor's own rows, e.g. the items a plan made). [] when the state moved meanwhile."""
    goal, items, settings = load(conn, goal_id)
    if item_id is not None:
        event = Event(event.kind, event.at, {**event.data, "item": item_id})
    with db.tx(conn):
        again = load(conn, goal_id)
        if again[0] != goal or again[1] != items:
            return []
        new_goal, new_items, actions = workstep.step(goal, tuple(items), settings, event)
        if then is not None:
            then(conn)
        now = db.now()
        if new_goal != goal:
            conn.execute("UPDATE goals SET state=?, work=?, error=CASE WHEN ? THEN ? ELSE error END WHERE id=?",
                         (goal_label(new_goal), db.dumps(asdict(new_goal)), new_goal.phase == "failed",
                          new_goal.why, goal_id))
        was = {x.id: x for x in items}
        for it in new_items:
            if was.get(it.id) != it:
                _write(conn, it, now)
        for a in actions:
            conn.execute("INSERT INTO work_actions(goal_id, item_id, action, why, created_at) VALUES (?,?,?,?,?)",
                         (goal_id, a.item, db.dumps({"kind": a.kind, "item": a.item, "data": a.data}), a.why, now))
    return list(actions)


def _write(conn: sqlite3.Connection, it: ItemState, now: float) -> None:
    """The item's state, its saved model and the columns the board reads; `error` says why it stopped."""
    stopped = it.phase == "stopped"
    conn.execute("UPDATE items SET state=?, work=?, tries=?, reviews=?, review=?, gate2=?, "
                 "error=CASE WHEN ? THEN ? ELSE error END, updated_at=? WHERE id=?",
                 (label(it), db.dumps(asdict(it)), it.tries, it.reviews, it.review, it.gate2, stopped, it.why,
                  now, it.id))


# ---- carry ----------------------------------------------------------------------------------

def _notify(conn: sqlite3.Connection, project: workproject.WorkProject, action: Action) -> List[str]:
    """A needs-you push, once per key (selfwork.needs_you)."""
    it = conn.execute("SELECT title FROM items WHERE id=?", (action.item,)).fetchone()
    d = action.data
    body = " ".join(f"{it['title'] if it else action.item} — {d.get('what') or action.why}: {d.get('act') or ''}"
                    .split())[:200]
    notify.queue(conn, d.get("key") or f"item:{action.item}:{action.why}", d.get("kind") or "needs_you",
                 "eki · needs you", body)
    return []


def _hold(conn: sqlite3.Connection, project: workproject.WorkProject, action: Action) -> List[str]:
    """The hold is in the item's saved state already: the scheduler reads it. Only say so."""
    return [f"item {action.item}: {action.why}"]


#: how many rounds of actions saved by handlers one carry does
CARRY_ROUNDS = 3

HANDLERS: Dict[str, Callable[[sqlite3.Connection, workproject.WorkProject, Action], List[str]]] = {
    "notify": _notify, "hold_until": _hold, **workhandlers_build.HANDLERS, **workhandlers_queue.HANDLERS}


def carry(conn: sqlite3.Connection) -> List[str]:
    """Do every action not yet done, oldest first. One with no handler yet waits; one whose
    handler fails is tried again next tick; one whose project is gone is dropped, said. Actions a
    handler's own feed saves (a second judge that can't start proposes and asks) are done in the
    same call, a few rounds deep."""
    said: List[str] = []
    projects: Dict[str, Optional[workproject.WorkProject]] = {}
    seen = 0
    for _ in range(CARRY_ROUNDS):
        rows = conn.execute("SELECT * FROM work_actions WHERE done_at IS NULL AND id>? ORDER BY id",
                            (seen,)).fetchall()
        if not rows:
            break
        seen = rows[-1]["id"]
        said += _carry(conn, rows, projects)
    return said


def _carry(conn: sqlite3.Connection, rows: List[sqlite3.Row],
           projects: Dict[str, Optional[workproject.WorkProject]]) -> List[str]:
    said: List[str] = []
    for r in rows:
        raw = _json(r["action"]) or {}
        action = Action(raw.get("kind") or "", raw.get("item"), r["why"] or "", raw.get("data") or {})
        handler = HANDLERS.get(action.kind)
        if handler is None:
            continue
        if r["goal_id"] not in projects:
            g = conn.execute("SELECT * FROM goals WHERE id=?", (r["goal_id"],)).fetchone()
            try:
                projects[r["goal_id"]] = workproject.of_goal(conn, g) if g is not None else None
            except KeyError:
                projects[r["goal_id"]] = None
        project = projects[r["goal_id"]]
        if project is None:
            said.append(f"work: {action.kind} for {action.item or r['goal_id']} dropped — its goal or project is gone")
        else:
            try:
                said += handler(conn, project, action)
            except Exception:                    # noqa: BLE001 — one action's trouble never stops the rest
                log.exception("work action %s (%s) failed; tried again next tick", r["id"], action.kind)
                continue
        conn.execute("UPDATE work_actions SET done_at=? WHERE id=? AND done_at IS NULL", (db.now(), r["id"]))
    return said
