"""The queue for a local project's items: rebase, resolve, gate 2 and landing
on the project's own branch (docs/self-build.md, "The queue").

It is eki's own queue run once per project: the same rules
(eki/workstep_queue.py) and the same stack plumbing (eki/queuestack.py); this
module is only a project's executor — which tip, which check, how it lands. A project whose
row has no GitHub repo and autonomy 'apply' (eki/projects.py) has one stack:
its queued items in order, each rebased onto the tip of refs/heads/<branch>
plus the items ahead of it. A conflict goes to the side path (resolve.py);
gate 2 is the project's check, run on the stack as a batch with queue's
green/red/bisect rules, one check per project at a time — a red one waits for
the green ones under it to land, so its fix starts on the branch they made;
with no check the items aren't judged. Items of different projects never share a batch.

A project with a workflow card (eki/workflow.py) follows it: without `batch`
in its checks the queue checks nothing together — each item is gate 2 green,
'not batched', and lands in order; with `batch` the card's command is the
check. Its merge says whether it lands here at all: only `auto` is admitted.

The front lands through projectland.land: refs/heads/<branch> moves forward,
and the person's folder fast-forwards when it has the branch checked out and
no file the change touches has local changes there. When one does, the item
waits and says which file. Never a force, a reset of the branch or a push.

A 'propose' project or a GitHub one is left alone: its proposed items wait
for the person (queue.settle) or go back as pull requests.

Nothing is kept in memory: each tick rebuilds what to do from the items
table, the runs and git. An item already in the branch is applied, so a
restart between moving the ref and writing the row loses nothing.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import List

from . import db, projectland, projects, projectwork, queuestack, selfwork, workflow, workproject, workspace
from .workhandlers_queue import event, say
from .workspace import WorkspaceError, git

log = logging.getLogger("eki.projectqueue")


def _projects(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Local projects that merge by themselves (merge auto) with a proposed or queued item."""
    rows = conn.execute(
        "SELECT DISTINCT p.* FROM items i JOIN goals g ON g.id=i.goal_id JOIN projects p ON p.id=g.project "
        "WHERE i.state IN ('proposed', 'queued') AND p.repo IS NULL AND p.branch IS NOT NULL "
        "AND COALESCE(p.state, 'on') NOT IN ('deleting', 'deleted') ORDER BY p.created_at").fetchall()
    return [p for p in rows if workproject.settings(conn, p).merge == "auto"]


def items(conn: sqlite3.Connection, pid: str, state: str) -> List[sqlite3.Row]:
    """The project's items in `state`; queued ones front first."""
    return conn.execute("SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? AND i.state=? "
                        "ORDER BY COALESCE(i.queued_at, i.created_at), i.created_at", (pid, state)).fetchall()


def tip(project: sqlite3.Row) -> str:
    return git(project["path"], "rev-parse", "--verify", f"refs/heads/{project['branch']}^{{commit}}")


# ---- the tick --------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    for project in _projects(conn):
        if not Path(project["path"]).is_dir():
            continue
        try:
            said += _admit(conn, project)
            said += queuestack.walk(conn, _queue(conn, project))
            said += land(conn, project)
        except (WorkspaceError, OSError) as e:          # one project's git trouble never stops the others
            log.warning("project queue: %s: %s", project["name"], e)
    return said


def _queue(conn: sqlite3.Connection, project: sqlite3.Row) -> queuestack.Queue:
    """The project's stack; with nothing checked together, the words that say so."""
    wf = workflow.of_project(conn, project)
    if wf is not None:
        verdict = "not batched: " + workflow.said(wf)
        line = f"gate 2 skipped — {verdict}"
    else:
        verdict = projectwork.not_judged(project)
        line = f"gate 2 not judged — {project['name']} has no check"
    return queuestack.Queue(rows=lambda: items(conn, project["id"], "queued"), tip=lambda: tip(project),
                            unbatched=(line, verdict),
                            batched="batch" in workproject.settings(conn, project).gates)


def _admit(conn: sqlite3.Connection, project: sqlite3.Row) -> List[str]:
    """What git says of the project's proposed and queued items: in the branch already (a restart
    after the ref moved), or can it be queued (its worktree is there, with a commit)."""
    said: List[str] = []
    head = tip(project)
    for state in ("proposed", "queued"):
        for it in items(conn, project["id"], state):
            sha = (it["rebased"] or it["commit_sha"]) if state == "queued" else it["commit_sha"]
            there = bool(sha) and projectwork.is_ancestor(project["path"], sha, head)
            queueable = (state == "proposed" and bool(it["commit_sha"]) and bool(it["worktree"])
                         and Path(it["worktree"]).is_dir())
            if not there and not queueable:
                continue
            said += say(conn, it["goal_id"], it["id"],
                        event("seen", in_target=there, queueable=queueable, sha=sha, branch=project["branch"],
                              where=f"{(sha or '')[:12]} is in {project['name']} {project['branch']}",
                              onto=f"{project['name']} {project['branch']}",
                              **({"locked": [], "apply": projects.autonomy(project) == "apply"} if queueable else {})),
                        then=_landed(project), after=_gone(project))
    return said


def _landed(project: sqlite3.Row):
    """The columns of an item that landed on the project's branch."""
    def then(c: sqlite3.Connection, old, new) -> None:
        if new.stage == "applied" and old is not None and old.stage in ("queued", "gate 2"):
            selfwork._set(c, new.id, landed_at=db.now(), landed_on=project["branch"], why=None)
        elif new.stage == "queued" and new.why.startswith("waits: "):
            selfwork._set(c, new.id, why=new.why)
    return then


def _gone(project: sqlite3.Row):
    def after(c: sqlite3.Connection, old, new) -> None:
        if new.stage == "applied":
            workspace.remove(project["path"], f"gate2-{new.id}")
    return after


# ---- landing ---------------------------------------------------------------------------------

def land(conn: sqlite3.Connection, project: sqlite3.Row, carry: bool = True) -> List[str]:
    """The front lands through projectland.land once gate 2 is green on it and it sits on the
    branch's tip; or it waits, saying why. The land handler's plumbing (eki/workhandlers_queue.py)."""
    said: List[str] = []
    for it in items(conn, project["id"], "queued"):
        if it["head"] != tip(project) or it["gate2"] != "green" or not it["rebased"]:
            break
        try:
            why = projectland.land(project["path"], project["branch"], it["rebased"])
        except projectland.NotFastForward as e:
            log.info("project queue: item %s: %s; rebased next tick", it["id"], e)
            break
        data = dict(went="target", sha=it["rebased"], branch=project["branch"])
        if why is not None:
            data["waits"] = f"{project['name']} {why}"
        said += say(conn, it["goal_id"], it["id"], event("landed", **data), then=_landed(project),
                    after=_gone(project), carry=carry)
        if why is not None:
            break
    return said
