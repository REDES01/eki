"""The build executor (docs/work-model.md): it sees which build, gate-1 and
review runs ended, does the I/O that tells their facts — commit what the
agent left, list what it touched, read the check's tail and the review's
word — makes an Event of them and feeds it to the work model. Every decision
is eki/workstep.py's; the actions it returns are carried by workrun.carry
(eki/workhandlers_build.py).

An item with an action not yet carried is skipped: its run_id still names
the run before, so concluding it again would decide twice. Signals map to
failure kinds here, from the stop eki/stopkind.py's classify stored on the
run (`runs.stop`): transient → transient, a missing run → run_lost, a run
whose `runs.stop` says environment → environment, held till its recheck
(eki/itemhold.py), and a red check, an early end, nothing changed or a crash
→ the judgment ladder.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from . import (callwait, db, deplinks, fixer, itemhold, judge, leftover, projects, projectwork, review, selfbrief, selfwork,
               store, workflow, workflowgates, workrun, workspace)
from .workmodel import Event

Then = Optional[Callable[[sqlite3.Connection], None]]


# ---- feeding ----------------------------------------------------------------------------------

def pending(conn: sqlite3.Connection, iid: str) -> bool:
    """Has the item an action saved and not yet carried out?"""
    return conn.execute("SELECT 1 FROM work_actions WHERE item_id=? AND done_at IS NULL LIMIT 1",
                        (iid,)).fetchone() is not None


def feed(conn: sqlite3.Connection, it: sqlite3.Row, event: Event, then: Then = None,
         error: Optional[str] = None) -> List[str]:
    """Feed the item's event; its board line when it moved. `error` is what `items.error` keeps
    when it stopped (the board's line says the model's words)."""
    hold = event.data.get("hold") if event.kind == "item_failed" else None
    if hold is not None:                # a broken setup: the hold the scheduler and the board read
        event = Event(event.kind, event.at, {**event.data, "key": itemhold.key(it["id"], hold["fix"])})
        then = _held(it["id"], hold, then)
    was = conn.execute("SELECT state, work FROM items WHERE id=?", (it["id"],)).fetchone()
    workrun.feed(conn, it["goal_id"], it["id"], event, then=then)
    cur = conn.execute("SELECT state, work FROM items WHERE id=?", (it["id"],)).fetchone()
    if cur is None or (was is not None and tuple(was) == tuple(cur)):
        return []
    if error is not None and cur["state"] in ("left", "unfit"):
        conn.execute("UPDATE items SET error=? WHERE id=? AND state=?", (error, it["id"], cur["state"]))
    why = (json.loads(cur["work"] or "null") or {}).get("why") or cur["state"]
    return [f"item {it['id']}: {why}"]


def feed_goal(conn: sqlite3.Connection, g: sqlite3.Row, event: Event, then: Then = None) -> List[str]:
    """Feed the goal's event (its id in the data, for the actions); its board line when it moved."""
    was = conn.execute("SELECT state, work FROM goals WHERE id=?", (g["id"],)).fetchone()
    workrun.feed(conn, g["id"], None, Event(event.kind, event.at, {**event.data, "goal": g["id"]}), then=then)
    cur = conn.execute("SELECT state, work FROM goals WHERE id=?", (g["id"],)).fetchone()
    if tuple(was) == tuple(cur):
        return []
    return [f"goal {g['id']}: {(json.loads(cur['work'] or 'null') or {}).get('why') or cur['state']}"]


def _held(iid: str, hold: Dict[str, Any], then: Then) -> Callable[[sqlite3.Connection], None]:
    def write(c: sqlite3.Connection) -> None:
        selfwork._set(c, iid, hold=db.dumps(hold), error=itemhold.error(hold))
        if then is not None:
            then(c)
    return write


def held(run: Optional[sqlite3.Row]) -> Optional[Event]:
    """A run that stopped on a broken setup (eki/itemhold.py): environment, rechecked later."""
    now = db.now()
    hold = itemhold.environment(run, now)
    if hold is None:
        return None
    return Event("item_failed", now, {"kind": "environment", "retry_at": hold["retry_at"], "fix": hold["fix"],
                                      "why": f"setup broken: {hold['fix']}", "hold": hold})


def ended(run: sqlite3.Row, what: str) -> Event:
    """A run that ended not done: held on a broken setup, transient when it reads so (no retry
    time: the worker's own retries are spent), else the run's end as it is."""
    if (ev := held(run)) is not None:
        return ev
    if (itemhold.stop_of(run) or {}).get("kind") == "transient":
        return Event("item_failed", db.now(), {"kind": "transient", "retry_at": None, "fix": None,
                                               "why": f"the {what} run ended {run['state']}"})
    return Event("item_finished", db.now(), {"ended": run["state"], "error": run["error"]})


def _set_then(iid: str, **fields: Any) -> Callable[[sqlite3.Connection], None]:
    return lambda c: selfwork._set(c, iid, **fields)


# ---- starting -----------------------------------------------------------------------------

def start(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """The scheduler has room for a waiting item: True when the model started it."""
    recheck = itemhold.hold_of(it) is not None          # its broken setup's recheck: no try spent
    feed(conn, it, Event("item_started", db.now(), {"recheck": recheck}),
         then=_set_then(it["id"], why=None, error=None, hold=None))
    cur = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
    return cur is not None and cur["state"] == "building"


# ---- a build ended ------------------------------------------------------------------------

def conclude_build(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if (run is not None and run["state"] in store.ACTIVE) or pending(conn, it["id"]):
        return []
    if run is None:                                  # lost: the same build again, not a try
        return feed(conn, it, Event("run_lost", db.now(), {}))
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    project = projectwork.project_of(conn, goal)
    if run["state"] == "done":
        got = callwait.facts(conn, it, run, "building")
        if got is not None and got["wakes"] < callwait.MAX_WAITS:   # its card calls are out
            waits = got.pop("waits")
            return feed(conn, it, Event("item_finished", db.now(), got),
                        then=_set_then(it["id"], waits=db.dumps(waits)))
    killed = leftover.kill(leftover.find(it["worktree"])) if it["worktree"] else []
    left = (f"item {it['id']}: the build left background tasks running — killed: "
            + ", ".join(f"{p['pid']} {p['cmd']}" for p in killed)) if killed else None
    if left:                                         # said on the board and in the thread; judging goes on
        store.add_event(conn, run["id"], run["attempt"], "note", {"text": left})
    said = ([left] if left else []) + callwait.stopped(conn, it, run)
    if run["state"] != "done":
        return said + feed(conn, it, ended(run, "build"),
                           error=f"the build run ended {run['state']}: {run['error'] or ''}")
    relinked = deplinks.restore(it["worktree"], project["path"] if project is not None else selfwork.repo())
    if not relinked:
        return said + built(conn, it, run, goal, project)
    why = (f"relinked {', '.join(relinked)}: the agent installed inside the worktree;"
           f" dependencies come from the project folder")
    selfwork._set(conn, it["id"], why=why)
    return said + [f"{line} — {why}" for line in built(conn, it, run, goal, project)]


def built(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row, goal: sqlite3.Row,
          project: Optional[sqlite3.Row], fixed: bool = False) -> List[str]:
    """A done build or fixer run: commit what it left, then feed what came of it."""
    answer = store.answer(conn, run["id"])
    verdict, reason, summary = selfbrief.outcome(answer)
    try:
        sha = workspace.commit_all(it["worktree"], f"{'eki' if project else 'self'}: {it['title']}\n\n"
                                                    f"{goal['text']}"[:2000])
    except (workspace.WorkspaceError, OSError) as e:      # one item's git trouble stops one item
        return feed(conn, it, Event("item_finished", db.now(), {"commit_error": str(e)}),
                    error=selfwork.with_tried(it, f"couldn't commit the change: {e}"[:800]))
    sha = sha or it["commit_sha"]           # a retry that left things as they were: judge again
    touched = workspace.changed(it["worktree"], selfwork.fork_point(it, project)) if sha else []
    d: Dict[str, Any] = {"verdict": verdict, "reason": reason, "changed": sha is not None, "touched": touched,
                         "branch": it["branch"], "can_fix": fixer.can_start(conn, it)}
    why = reason or ("the agent changed nothing" if sha is None else "")
    if verdict != "person" and sha is None:
        d.update(ended_early=fixer.ended_early(answer), tail=f"{why}\n{selfwork.failure_tail(answer, 20)}")
    fields: Dict[str, Any] = dict(commit_sha=sha, touched=db.dumps(touched),
                                  error=None if verdict == "done" else f"partial: {reason}")
    if not fixed:                           # the builder's summary stays; the fixer's is in `fixer`
        fields["summary"] = summary
    unjudged = _unjudged(conn, it, project, touched)
    if unjudged is not None:                # no gate 1 to run: the builder's word stands
        d.update(said=unjudged[0], no_review=unjudged[1])
        fields.update(verdict=unjudged[0], judged_by=None)
    return feed(conn, it, Event("item_finished", db.now(), d), then=_set_then(it["id"], **fields),
                error=selfwork.with_tried(it, why))


def _unjudged(conn: sqlite3.Connection, it: sqlite3.Row, project: Optional[sqlite3.Row],
              touched: List[str]) -> Optional[tuple]:
    """(what the item says, why no review) when a project's item has no gate-1 command to run."""
    if project is None:
        return None
    wf = workflow.of_project(conn, project)
    if wf is not None:
        if workflowgates.gate(wf, it["title"], touched) is None:
            return workflow.said(wf), workflowgates.NO_REVIEW
        return None
    return (projectwork.not_judged(project), None) if projects.check_argv(project) is None else None


# ---- gate 1 and the judge -----------------------------------------------------------------

def conclude_judging(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Gate 1 and its review (if any) have both ended: the check_verdict event."""
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE or not review.ended(conn, it) or pending(conn, it["id"]):
        return []
    if (ev := held(run)) is not None:               # a broken setup: held, no try spent
        seen, result = review.said(conn, it)
        return feed(conn, it, ev, then=lambda c: review.close(c, it, seen, result))
    tail = selfwork.failure_tail(store.answer(conn, run["id"]))
    if run["state"] != "done":
        tail = (tail + f"\n(checks {run['state']}: {selfwork.failure_tail(run['error'] or '', 6)})").strip()
    green = run["state"] == "done"
    d = {"result": "green" if green else "red", "ended": run["state"], "tail": tail,
         "can_fix": fixer.can_start(conn, it), "second": judge.is_second(conn, it)}
    return verdict(conn, it, d, {"verdict": tail[-800:] if green else tail[-selfwork.FAILURE_CHARS:]},
                   error=selfwork.with_tried(it, f"checks {run['state']}"))


def verdict(conn: sqlite3.Connection, it: sqlite3.Row, d: Dict[str, Any],
            fields: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> List[str]:
    """Feed check_verdict with the judge's word; an older review's chore is closed with the state written."""
    seen, result = review.said(conn, it)

    def then(c: sqlite3.Connection) -> None:
        review.close(c, it, seen, result)
        if fields:
            selfwork._set(c, it["id"], **fields)
    return feed(conn, it, Event("check_verdict", db.now(), {"branch": it["branch"], **d, "review": seen}),
                then=then, error=error)
