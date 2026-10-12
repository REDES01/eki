"""eki builds eki: goals, items, and the runs that carry each item through
plan → build → judge (gate 1 and the review side by side) → proposed
(docs/self-build.md).

Everything here is a run in an ordinary thread, so a restart loses nothing
and `eki follow` shows any of it. The engine calls `tick` every couple of
seconds; it looks at what finished since and feeds each change to the work
model (eki/workstep.py decides, eki/workrun.py saves and carries out;
eki/buildfeed.py is the executor), so each goal or item moves on exactly
once. A fit item is a branch `self/<id>` with its checks green.

Branches, worktrees and file listings live in the integration repo eki owns
(eki/integration.py, EKI_HOME/self/repo): the source checkout (~/eki) is
never touched by self-work. `source()` is still the source checkout — the
folder agents are told to stay out of; `repo()` is where the branches are.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import (buildfeed, builds, callwait, db, fixer, integration, itemhold, notify, paths, projects, projectwork,
               review, scheduler, selfdraft, selfplan, stopkind, store, workflow, workflowgates, workrun, workspace)
from .workmodel import Event

log = logging.getLogger("eki.self")

DEFAULTS = {"parallel": 4, "autonomy": "propose",
            "loop": False, "review_max": 3, "picks_per_day": 6, "pick_min_cluster": 3,   # eki/selfpick.py
            "standing_rest_hours": 24, "standing_waiting_max": 3,                        # eki/standing.py
            "brief_wait": 10,                                                            # eki/faults.py
            "issues_minutes": 10, "pr_followups": 3, "pr_mirror": False}                 # eki/github.py
#: an item in one of these has a run going and holds its write-set
LIVE = ("building", "judging", "reviewing", "waiting on calls", "fixing")
#: gate 1, run in the worktree with its own (linked) venv, whatever the engine's environment says
CHECK = '["/bin/sh", "-c", "EKI_PYTHON=$PWD/.venv/bin/python EKI_CHECK_FAST=1 exec bin/check -q"]'   # drills run once, at release (traincheck)


def settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text()).get("self") or {}
    except (OSError, ValueError):
        got = {}
    out = {**DEFAULTS, **got}
    wf = workflowgates.own()           # eki's own workflow card, when it has one, says these
    if wf is not None:
        out.update(autonomy=workflow.autonomy(wf), parallel=wf.parallel, review=workflow.has(wf, "review"))
    return out


def source() -> Path:
    return builds.source()


def repo() -> Path:
    return integration.repo()


# ---- in ---------------------------------------------------------------------------------

def submit(conn: sqlite3.Connection, text: str, *, plan: bool = True, draft: bool = False,
           files: Optional[List[str]] = None, owner: str = "you", source_kind: str = "ask",
           why: Optional[str] = None, pick_key: Optional[str] = None,
           project: Optional[str] = None, standing_id: Optional[str] = None,
           issue: Optional[int] = None) -> str:
    """A goal. With `draft` the text is a wish the draft run makes into a goal
    first (eki/selfdraft.py); without `plan` it is one item as it stands.
    With `project` it is work on a person's own repo (eki/projects.py). A workflow
    card saying plan single makes it one item whatever `plan` says."""
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to do")
    plan = plan and not workflowgates.single(conn, project)
    if project is not None and projects.get(conn, project) is None:
        raise ValueError(f"no project {project}")
    gid, iid = store.new_id(), store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, wish, source, owner, state, created_at, why, pick_key, "
                     "project, standing_id, issue) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (gid, text, text, source_kind, owner, "planning" if plan else "planned", db.now(),
                      why, pick_key, project, standing_id, issue))

    def one(c: sqlite3.Connection) -> None:
        if not plan:
            new_item(c, gid, text.splitlines()[0][:120], text, files or [], [], False, iid=iid)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    buildfeed.feed_goal(conn, g, Event("goal_submitted", db.now(), {"plan": plan, "draft": draft, "item": iid,
                                                                   "files": files or []}), then=one)
    workrun.carry(conn)                 # the draft or plan run starts now, not at the next tick
    return gid


def new_item(conn: sqlite3.Connection, gid: str, title: str, spec: str, files: List[str],
             deps: List[str], independent: bool, context: Optional[List[dict]] = None,
             iid: Optional[str] = None) -> str:
    iid = iid or store.new_id()
    now = db.now()
    conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, context, "
                 "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (iid, gid, title, spec, db.dumps(files), db.dumps(deps), int(independent),
                  db.dumps(context) if context is not None else None, now, now))
    return iid


# ---- the tick -----------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    for g in conn.execute("SELECT * FROM goals WHERE state='drafting'").fetchall():
        said += selfdraft.conclude(conn, g)
    for g in conn.execute("SELECT * FROM goals WHERE state='planning'").fetchall():
        said += workflowgates.told(conn, g, selfplan.conclude_plan(conn, g))
    for it in items_in(conn, ("building",)):
        said += buildfeed.conclude_build(conn, it)
    for it in items_in(conn, ("judging",)):
        said += buildfeed.conclude_judging(conn, it)
    for it in items_in(conn, ("reviewing",)):
        said += review.conclude(conn, it)
    said += callwait.tick(conn)          # turns that ended with card calls out: woken once they're back
    said += _settle_applied(conn)
    said += workflowgates.started(conn, scheduler.start_ready(conn))
    return said + workrun.carry(conn)


def _settle_applied(conn: sqlite3.Connection) -> List[str]:
    """A proposed item whose commit is now in main — integration main, or the
    source's main where you merged it by hand — is applied; items that depend
    on it may start (from a base that has it). A project's item is applied
    once you merged it into the project's branch."""
    said = []
    own, theirs = projectwork.split(conn, items_in(conn, ("proposed",)))
    for it, project in theirs:
        if it["commit_sha"] and projectwork.merged(project, it["commit_sha"]):
            _set(conn, it["id"], state="applied")
            said.append(f"item {it['id']}: applied — {it['commit_sha'][:12]} is in {project['name']}"
                        f" {project['branch']}")
    if not own:
        return said
    main = workspace.head(source())
    for it in own:
        if it["commit_sha"] and (integration.contains(it["commit_sha"]) or _is_ancestor(it["commit_sha"], main)):
            _set(conn, it["id"], state="applied")
            said.append(f"item {it['id']}: applied — {it['commit_sha'][:12]} is in main ({main[:12]})")
    return said


def _is_ancestor(commit: str, head: str) -> bool:
    """Is `commit` in `head`'s history (in the source checkout)?"""
    return projectwork.is_ancestor(source(), commit, head)


def items_in(conn: sqlite3.Connection, states: tuple) -> List[sqlite3.Row]:
    """Items in these states, oldest first. Waiting items `held` for a brief, or held
    for a broken setup until their recheck (eki/itemhold.py), are left out: they
    aren't ready, and the scheduler is who asks for waiting ones."""
    marks = ",".join("?" * len(states))
    rows = conn.execute(f"SELECT * FROM items WHERE state IN ({marks}) ORDER BY created_at",
                        states).fetchall()
    if "waiting" not in states:
        return rows
    wait, now = held(conn), db.now()
    return [r for r in rows if not (r["state"] == "waiting" and (r["goal_id"] in wait or itemhold.waits(r, now)))]


def held(conn: sqlite3.Connection) -> set:
    """Goals whose fault brief (eki/faults.py) is still open and younger than `self.brief_wait` minutes."""
    since = db.now() - float(settings().get("brief_wait", 10)) * 60
    return {r[0] for r in conn.execute("SELECT subject FROM chores WHERE kind='brief' AND state='open'"
                                       " AND created_at>?", (since,))}


def _set(conn: sqlite3.Connection, iid: str, **fields: Any) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), iid))


def needs_you(conn: sqlite3.Connection, it: sqlite3.Row, state: str, what: str, act: str, kind: str) -> bool:
    """Queue a needs-you push for the item that just went to `state` (inside the caller's transaction).
    Only a stop of a kind that is a person's (`stopkind.PINGS`) pings: never quota, transient or lost."""
    if kind not in stopkind.PINGS:
        return False
    body = " ".join(f"{it['title']} — {what}: {act}".split())[:200]
    return notify.queue(conn, f"item:{it['id']}:{state}:{it['tries']}", "needs_you", "eki · needs you", body)


def fork_point(it: sqlite3.Row, project: Optional[sqlite3.Row]) -> str:
    """Where the item's own work starts: its base, or — when the agent rebased the
    branch onto a newer head during its turn — the fork from that head, so the
    commits that landed meanwhile don't count as the item's (nor lock it)."""
    base = it["base"]
    upstream = project["branch"] if project is not None else "main"
    fork = workspace.git(it["worktree"], "merge-base", "HEAD", upstream, check=False).strip()
    if not fork or not base or fork == base:
        return base
    return fork if projectwork.is_ancestor(it["worktree"], base, fork) else base


def with_tried(it: sqlite3.Row, error: str) -> str:
    """An item that had its fixer run and is left anyway says what the fixer tried."""
    tried = fixer.tried(it)
    return f"{error} — {tried}" if tried else error


#: what a builder is shown of a failed check (its retry brief) and the item keeps as its verdict
FAILURE_LINES = 40
FAILURE_CHARS = 2500
_FRAME = re.compile(r"^\s+at \S.*(:\d+|\))\s*$|^Node\.js v\d")


def failure_tail(text: str, lines: int = FAILURE_LINES) -> str:
    """The end of a check's output a builder can act on. Stack frames (a JS `    at …` line,
    Node's version line) are dropped first: a build tool that prints its errors and then
    throws would otherwise fill the tail with the trace and hide the errors themselves."""
    kept = [ln for ln in (text or "").strip().splitlines() if not _FRAME.match(ln)]
    return "\n".join(kept[-lines:])[-FAILURE_CHARS:]


def store_item(conn: sqlite3.Connection, iid: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM items WHERE id=? OR id LIKE ?", (iid, iid + "%")).fetchone()


# ---- out ---------------------------------------------------------------------------------------

def drop(conn: sqlite3.Connection, iid: str) -> str:
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    for rid in (it["run_id"], it["gate2_run"], it["review_run"], *((callwait.waits_of(it) or {}).get("calls") or [])):
        if rid:
            store.cancel(conn, rid)
    buildfeed.feed(conn, it, Event("person_decided", db.now(), {"decision": "drop"}))
    if it["worktree"]:
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
        where = projects.repo_for(conn, goal) if goal is not None else repo()
        workspace.remove(where, it["id"], delete_branch=(it["commit_sha"] is None))
    return it["id"]


def retry(conn: sqlite3.Connection, iid: str) -> str:
    """Build it again, a fresh fixer with it; afresh when its worktree is gone (it was dropped)."""
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    fresh = bool(it["worktree"] and not Path(it["worktree"]).exists())
    fields: Dict[str, Any] = {"error": None, "fixer": None, "hold": None}     # held: start it now
    if fresh:
        fields.update(worktree=None, branch=None, base=None, commit_sha=None, touched="[]", verdict=None,
                      summary=None, review_run=None)
    buildfeed.feed(conn, it, Event("person_decided", db.now(), {"decision": "retry", "fresh": fresh}),
                   then=lambda c: _set(c, it["id"], **fields))
    return it["id"]
