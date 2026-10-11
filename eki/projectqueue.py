"""The queue for a local project's items: rebase, resolve, gate 2 and landing
on the project's own branch (docs/self-build.md, "The queue").

It is eki's own queue (eki/queue.py) run once per project. A project whose
row has no GitHub repo and autonomy 'apply' (eki/projects.py) has one stack:
its queued items in order, each rebased onto the tip of refs/heads/<branch>
plus the items ahead of it. A conflict goes to the side path (resolve.py);
gate 2 is the project's check, run on the stack as a batch with queue's
green/red/bisect rules, one check per project at a time; with no check the
items aren't judged. Items of different projects never share a batch.

A project with a workflow card (eki/workflow.py) follows it: without `batch`
in its checks the queue checks nothing together — each item is gate 2 green,
'not batched', and lands in order; with `batch` the card's command is the
check. Its merge says whether it lands here at all: only `auto` is admitted.

The front lands through projectland.land: refs/heads/<branch> moves forward,
and the person's folder fast-forwards when it has the branch checked out and
no file the change touches has local changes there. When one does, the item
waits and says which file. Never a force, a reset of the branch or a push.

A 'propose' project or a GitHub one is left alone: its proposed items wait
for the person (selfwork._settle_applied) or go back as pull requests.

Nothing is kept in memory: each tick rebuilds what to do from the items
table, the runs and git. An item already in the branch is applied, so a
restart between moving the ref and writing the row loses nothing.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import List

from . import db, projectland, projects, projectwork, queue, rebase, selfwork, store, workflow, workspace
from .workspace import WorkspaceError, git

log = logging.getLogger("eki.projectqueue")


def _projects(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Local projects under 'apply' with a proposed or queued item."""
    rows = conn.execute(
        "SELECT DISTINCT p.* FROM items i JOIN goals g ON g.id=i.goal_id JOIN projects p ON p.id=g.project "
        "WHERE i.state IN ('proposed', 'queued') AND p.repo IS NULL AND p.branch IS NOT NULL "
        "AND COALESCE(p.state, 'on') NOT IN ('deleting', 'deleted') ORDER BY p.created_at").fetchall()
    return [p for p in rows if projects.autonomy(p) == "apply"]


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
            said += _already_in(conn, project)
            said += _walk(conn, project)
            said += _land(conn, project)
        except (WorkspaceError, OSError) as e:          # one project's git trouble never stops the others
            log.warning("project queue: %s: %s", project["name"], e)
    return said


def _admit(conn: sqlite3.Connection, project: sqlite3.Row) -> List[str]:
    said = []
    for it in items(conn, project["id"], "proposed"):
        if not it["commit_sha"] or not it["worktree"] or not Path(it["worktree"]).is_dir():
            continue
        if (it["review"] or "").startswith("no:"):     # objected twice: a person's call
            continue
        if queue._set_if(conn, it, "proposed", state="queued", queued_at=db.now(), head=None,
                         rebased=None, gate2=None, gate2_run=None, gate2_on=None):
            said.append(f"item {it['id']}: queued for {project['name']} {project['branch']}")
    return said


def _already_in(conn: sqlite3.Connection, project: sqlite3.Row) -> List[str]:
    """A queued item whose commit is in the branch already landed (a restart after the ref moved)."""
    said = []
    head = tip(project)
    for it in items(conn, project["id"], "queued"):
        sha = it["rebased"] or it["commit_sha"]
        if sha and projectwork.is_ancestor(project["path"], sha, head):
            said += _landed(conn, project, it, sha)
    return said


def _landed(conn: sqlite3.Connection, project: sqlite3.Row, it: sqlite3.Row, sha: str) -> List[str]:
    done = queue._set_if(conn, it, "queued", state="applied", landed_at=db.now(), landed_on=project["branch"],
                         gate2="green", why=None)
    workspace.remove(project["path"], f"gate2-{it['id']}")
    return [f"item {it['id']}: landed on {project['branch']} ({sha[:12]})"] if done else []


def _walk(conn: sqlite3.Connection, project: sqlite3.Row, again: bool = True) -> List[str]:
    queued = items(conn, project["id"], "queued")
    if not queued:
        return []
    head = tip(project)
    said: List[str] = []
    stack: List[sqlite3.Row] = []
    for it in queued:
        if it["head"] != head or not it["rebased"]:
            sha, lines = queue._rebase(conn, it, head)
            said += lines
            if sha is None:
                continue                                # out of the stack: head doesn't advance past it
            it = selfwork.store_item(conn, it["id"])
        stack.append(it)
        head = it["rebased"]
    said += _judge(conn, project, stack)
    if again and len(items(conn, project["id"], "queued")) < len(stack):
        said += _walk(conn, project, again=False)       # one went unfit: those behind it move up now
    return said


def _judge(conn: sqlite3.Connection, project: sqlite3.Row, stack: List[sqlite3.Row]) -> List[str]:
    wf = workflow.of_project(conn, project)
    if wf is not None and not workflow.has(wf, "batch"):
        return _not_batched(conn, wf, stack)
    argv = projects.check_argv(project)
    if argv is not None:
        return queue._judge(conn, stack, start=lambda c, s, lo, top: _start_gate2(c, project, argv, s, lo, top))
    said = []
    for it in stack:                                    # nothing to judge with: not judged, green
        if it["gate2"] != "green" and queue._set_if(conn, it, "queued", gate2="green",
                                                    verdict=projectwork.not_judged(project)):
            said.append(f"item {it['id']}: gate 2 not judged — {project['name']} has no check")
    return said


def _not_batched(conn: sqlite3.Connection, wf: workflow.Workflow, stack: List[sqlite3.Row]) -> List[str]:
    """The card has no batch check: nothing is checked together, each item stands green in order."""
    verdict = "not batched: " + workflow.said(wf)
    said = []
    for it in stack:
        if it["gate2"] != "green" and queue._set_if(conn, it, "queued", gate2="green", verdict=verdict):
            said.append(f"item {it['id']}: gate 2 skipped — {verdict}")
    return said


def _start_gate2(conn: sqlite3.Connection, project: sqlite3.Row, argv: str, stack: List[sqlite3.Row],
                 lo: int, top: int) -> List[str]:
    """One run of the project's check on stack[top], judging stack[lo..top] together."""
    batch = stack[lo:top + 1]
    it = stack[top]
    tree = rebase.gate_tree(project["path"], f"gate2-{it['id']}", it["rebased"])
    goals = {b["goal_id"] for b in batch}
    owners = {r["owner"] for r in conn.execute(
        f"SELECT owner FROM goals WHERE id IN ({','.join('?' * len(goals))})", tuple(goals))}
    with db.tx(conn):
        row = conn.execute("SELECT state, rebased FROM items WHERE id=?", (it["id"],)).fetchone()
        if row["state"] != "queued" or row["rebased"] != it["rebased"]:
            return []
        title = it["title"] if len(batch) == 1 else f"batch of {len(batch)}, up to {it['title']}"
        tid = store.create_thread(conn, f"gate 2: {title}", str(tree), by="eki")
        rid = store.create_run(conn, tid, argv, provider="command",
                               priority="now" if "you" in owners else "background")
        selfwork._set(conn, it["id"], gate2_run=rid, gate2_on=it["rebased"], gate2=None)
    what = f"item {it['id']}" if len(batch) == 1 else f"a batch of {len(batch)} ({batch[0]['id']}..{it['id']})"
    return [f"gate 2 on {what} of {project['name']} at {it['rebased'][:12]} ({rid[:8]})"]


# ---- landing ---------------------------------------------------------------------------------

def _land(conn: sqlite3.Connection, project: sqlite3.Row) -> List[str]:
    said: List[str] = []
    for it in items(conn, project["id"], "queued"):
        if it["head"] != tip(project) or it["gate2"] != "green" or not it["rebased"]:
            break
        try:
            why = projectland.land(project["path"], project["branch"], it["rebased"])
        except projectland.NotFastForward as e:
            log.info("project queue: item %s: %s; rebased next tick", it["id"], e)
            break
        if why is not None:
            waits = f"waits: {project['name']} {why}"
            if it["why"] != waits and queue._set_if(conn, it, "queued", why=waits):
                said.append(f"item {it['id']}: {waits}")
            break
        said += _landed(conn, project, it, it["rebased"])
    return said
