"""eki builds eki: goals, items, and the runs that carry each item through
plan → build → judge (gate 1 and the review side by side) → proposed
(docs/self-build.md).

Everything here is a run in an ordinary thread, so a restart loses nothing
and `eki follow` shows any of it. The engine calls `tick` every couple of
seconds; it looks at what finished since and moves each goal or item on
exactly once. A fit item is a branch `self/<id>` with its checks green.

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

from . import (builds, db, deplinks, doccheck, integration, leftover, notify, paths, projectbrief, projects,
               projectwork, review, scheduler, selfbrief, selfdraft, selfplan, store, workflow, workflowgates,
               workspace)

log = logging.getLogger("eki.self")

DEFAULTS = {"parallel": 4, "autonomy": "propose",
            "loop": False, "review_max": 3, "picks_per_day": 6, "pick_min_cluster": 3,   # eki/selfpick.py
            "standing_rest_hours": 24, "standing_waiting_max": 3,                        # eki/standing.py
            "brief_wait": 10,                                                            # eki/faults.py
            "issues_minutes": 10, "pr_followups": 3, "pr_mirror": False}                 # eki/github.py
#: a dependency is fit enough for its dependents to start: under "propose" once it is judged fit;
#: under "apply" once it is in integration main, so the dependent is built on top of it
#: a dependency counts once its code is in main — whoever put it there. Under
#: propose that is the person's apply: a dependent that started from a base
#: without its dependency would build against code it can't see
FIT = {"propose": ("landed", "live", "applied"),
       "apply": ("landed", "live", "applied")}
#: an item in one of these has a run going and holds its write-set
LIVE = ("building", "judging", "reviewing")
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
    if project is None:
        base = integration.sync()
    else:
        row = projects.get(conn, project)
        if row is None:
            raise ValueError(f"no project {project}")
        base = projects.base(row)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, wish, source, owner, state, created_at, why, pick_key, "
                     "project, standing_id, issue) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (gid, text, text, source_kind, owner, "planning" if plan else "planned", db.now(),
                      why, pick_key, project, standing_id, issue))
        if plan and draft:
            selfdraft.start_draft(conn, gid, text, base, owner)
        elif plan:
            selfdraft.start_plan(conn, gid, text, base, owner)
        else:
            new_item(conn, gid, text.splitlines()[0][:120], text, files or [], [], False)
    return gid


def new_item(conn: sqlite3.Connection, gid: str, title: str, spec: str, files: List[str],
             deps: List[str], independent: bool, context: Optional[List[dict]] = None) -> str:
    iid = store.new_id()
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
        said += _conclude_build(conn, it)
    for it in items_in(conn, ("judging",)):
        said += _conclude_judge(conn, it)
    for it in items_in(conn, ("reviewing",)):
        said += review.conclude(conn, it)
    said += _settle_applied(conn)
    said += workflowgates.started(conn, scheduler.start_ready(conn))
    return said


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
    """Items in these states, oldest first. Waiting items `held` for a brief are
    left out: they aren't ready, and the scheduler is who asks for waiting ones."""
    marks = ",".join("?" * len(states))
    rows = conn.execute(f"SELECT * FROM items WHERE state IN ({marks}) ORDER BY created_at",
                        states).fetchall()
    if "waiting" not in states:
        return rows
    wait = held(conn)
    return [r for r in rows if not (r["state"] == "waiting" and r["goal_id"] in wait)]


def held(conn: sqlite3.Connection) -> set:
    """Goals whose fault brief (eki/faults.py) is still open and younger than `self.brief_wait` minutes."""
    since = db.now() - float(settings().get("brief_wait", 10)) * 60
    return {r[0] for r in conn.execute("SELECT subject FROM chores WHERE kind='brief' AND state='open'"
                                       " AND created_at>?", (since,))}


def _set(conn: sqlite3.Connection, iid: str, **fields: Any) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), iid))


def needs_you(conn: sqlite3.Connection, it: sqlite3.Row, state: str, what: str, act: str) -> bool:
    """Queue a needs-you push for the item that just went to `state` (inside the caller's transaction)."""
    body = " ".join(f"{it['title']} — {what}: {act}".split())[:200]
    return notify.queue(conn, f"item:{it['id']}:{state}:{it['tries']}", "needs_you", "eki · needs you", body)


def _conclude_build(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE:
        return []
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    project = projectwork.project_of(conn, goal)
    killed = leftover.kill(leftover.find(it["worktree"])) if it["worktree"] else []
    left = (f"item {it['id']}: the build left background tasks running — killed: "
            + ", ".join(f"{p['pid']} {p['cmd']}" for p in killed)) if killed else None
    with db.tx(conn):
        if conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()["state"] != "building":
            return []
        if left:                                     # said on the board and in the thread; judging goes on
            store.add_event(conn, run["id"], run["attempt"], "note", {"text": left})
        said = [left] if left else []
        if run["state"] != "done":
            _set(conn, it["id"], state="left", error=f"the build run ended {run['state']}: {run['error'] or ''}")
            return said + [f"item {it['id']}: left — the build run ended {run['state']}"]
        relinked = deplinks.restore(it["worktree"], project["path"] if project is not None else repo())
        if not relinked:
            return said + _built(conn, it, run, goal, project)
        why = (f"relinked {', '.join(relinked)}: the agent installed inside the worktree;"
               f" dependencies come from the project folder")
        _set(conn, it["id"], why=why)
        return said + [f"{line} — {why}" for line in _built(conn, it, run, goal, project)]


def _built(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row, goal: sqlite3.Row,
           project: Optional[sqlite3.Row]) -> List[str]:
    """A done build: commit what it left, then propose it or start gate 1 and the review."""
    verdict, reason, summary = selfbrief.outcome(store.answer(conn, run["id"]))
    try:
        sha = workspace.commit_all(it["worktree"], f"{'eki' if project else 'self'}: {it['title']}\n\n"
                                                    f"{goal['text']}"[:2000])
    except (workspace.WorkspaceError, OSError) as e:      # one item's git trouble stops one item
        _set(conn, it["id"], state="left", error=f"couldn't commit the change: {e}"[:800])
        return [f"item {it['id']}: left for you — couldn't commit: {str(e)[:120]}"]
    sha = sha or it["commit_sha"]           # a retry that left things as they were: judge again
    touched = workspace.changed(it["worktree"], it["base"]) if sha else []
    if verdict == "person" or sha is None:
        why = reason or ("the agent changed nothing" if sha is None else "")
        _set(conn, it["id"], state="left", error=why, summary=summary, commit_sha=sha,
             touched=db.dumps(touched))
        if verdict == "person":                 # "changed nothing" is a failed try, not a question
            needs_you(conn, it, "left", f"left for you: {why}", f"eki self show {it['id']}")
        return [f"item {it['id']}: left for you — {why}"]
    error = None if verdict == "done" else f"partial: {reason}"
    wf = workflow.of_project(conn, project) if project is not None else None
    if wf is not None:                 # the project's workflow card chooses the gates
        got = workflowgates.gate(wf, it["title"])
        if got is None:                # checks: self only — the builder's own word stands
            _set(conn, it["id"], state="proposed", commit_sha=sha, summary=summary, touched=db.dumps(touched),
                 verdict=workflow.said(wf), error=error, judged_by=None, review=f"none: {workflowgates.NO_REVIEW}")
            return [f"item {it['id']}: built — proposed on {it['branch']} ({workflow.said(wf)})"]
        judge = got[0]
        _set(conn, it["id"], judged_by=got[1])
    elif project is not None:
        judge = projects.check_argv(project)
        if judge is None:              # nothing to judge with: proposed as it is
            _set(conn, it["id"], state="proposed", commit_sha=sha, summary=summary, touched=db.dumps(touched),
                 verdict=projectwork.not_judged(project), error=error, judged_by=None)
            return [f"item {it['id']}: built — proposed on {it['branch']} ({projectwork.not_judged(project)})"]
        _set(conn, it["id"], judged_by=_judged_by(project, judge))
    else:
        judge = doccheck.command(it["base"], sha) if doccheck.docs_only(touched) else CHECK
    rid = store.create_run(conn, it["thread_id"], judge, provider="command", priority=run["priority"])
    _set(conn, it["id"], state="judging", run_id=rid, commit_sha=sha, summary=summary,
         touched=db.dumps(touched), error=error)
    rev, why = review.ask(conn, store_item(conn, it["id"]))     # a second reader beside gate 1
    _set(conn, it["id"], review_run=rev, review=None if rev else f"none: {why}")
    return [f"item {it['id']}: built ({len(touched)} files); judging"
            + (f" and reviewing ({rev[:8]})" if rev else "")]


def _judged_by(project: sqlite3.Row, judge: str) -> str:
    """The check's command and where it came from: `make x (set)`, `npm run test (guessed: …)`."""
    cmd = json.loads(judge)[2]
    source = project["check_from"] if "check_from" in project.keys() else None
    if not source:
        return cmd
    return f"{cmd} ({source})" if source == "set" or source.startswith("guessed: ") else cmd


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


def _conclude_judge(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Move a `judging` item once gate 1 and its review (if any) have both ended."""
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE or not review.ended(conn, it):
        return []
    tail = failure_tail(store.answer(conn, run["id"]))
    if run["state"] != "done":
        tail = (tail + f"\n(checks {run['state']}: {failure_tail(run['error'] or '', 6)})").strip()
    with db.tx(conn):
        cur = conn.execute("SELECT state, reviews FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != "judging":
            return []
        seen = it["review"]
        if it["review_run"]:
            said, why = review.read(conn, it)                # closes the chore (eki/review.py)
            seen = f"{said}: {why}" if said == "no" else said or f"none: {why}"
        if run["state"] == "done":
            return _judged(conn, it, tail, seen, cur["reviews"])
        if it["tries"] - (cur["reviews"] or 0) < 2 and run["state"] == "failed":   # a review's rebuild is free
            _set(conn, it["id"], state="waiting", verdict=tail[-FAILURE_CHARS:], review=seen,
                 error="checks failed; trying once more")
            return [f"item {it['id']}: checks failed; one more try"]
        _set(conn, it["id"], state="unfit", verdict=tail[-FAILURE_CHARS:], review=seen, error=f"checks {run['state']}")
    return [f"item {it['id']}: unfit — left for you"]


def _judged(conn: sqlite3.Connection, it: sqlite3.Row, tail: str, seen: Optional[str],
            reviews: Optional[int]) -> List[str]:
    """Gate 1 green: the review's word decides (under the caller's tx)."""
    if not (seen or "").startswith("no:"):
        _set(conn, it["id"], state="proposed", verdict=tail[-800:], review=seen)
        how = "reviewed ok" if seen == "ok" else f"fit (not reviewed: {(seen or 'none: ?')[6:]})"
        return [f"item {it['id']}: {how} — proposed on {it['branch']}"]
    why = seen[3:].strip()
    if not reviews:
        _set(conn, it["id"], state="waiting", verdict=None, review=seen, reviews=1,
             error="review: no — rebuilding once")
        return [f"item {it['id']}: the review says no — {why[:120]}; rebuilding once"]
    _set(conn, it["id"], state="proposed", verdict=tail[-800:], review=seen)
    return [f"item {it['id']}: the review says no again — proposed; `eki self apply {it['id']}` if you agree"]


# ---- starting a build -------------------------------------------------------------------------

def start_build(conn: sqlite3.Connection, it: sqlite3.Row, goal: sqlite3.Row, others) -> None:
    project = projectwork.project_of(conn, goal)
    if not it["worktree"]:
        if project is None:
            base, branch = integration.sync(), f"self/{it['id']}"
            wt = workspace.add(repo(), it["id"], base=base, branch=branch)
        else:
            wt, base, branch = projectwork.worktree(project, it["id"])
        tid = store.create_thread(conn, f"{project['name'] if project else 'self'}: {it['title']}", str(wt), by="eki")
        _set(conn, it["id"], worktree=str(wt), branch=branch, base=base, thread_id=tid)
        it = store_item(conn, it["id"])
    failure = it["verdict"] if it["tries"] else None
    brief = dict(goal=goal["text"], title=it["title"], spec=it["spec"], files=json.loads(it["files"] or "[]"),
                 branch=it["branch"], base=it["base"], source=str(source()), others=others, failure=failure,
                 objection=review.objection(it))
    prompt = selfbrief.build(**brief) if project is None else projectbrief.build(**brief, project=project)
    rid = store.create_run(conn, it["thread_id"], prompt, row="code",
                           priority="now" if goal["owner"] == "you" else "background")
    _set(conn, it["id"], state="building", run_id=rid, tries=it["tries"] + 1, error=None)


def store_item(conn: sqlite3.Connection, iid: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM items WHERE id=? OR id LIKE ?", (iid, iid + "%")).fetchone()


# ---- out ---------------------------------------------------------------------------------------

def drop(conn: sqlite3.Connection, iid: str) -> str:
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    for rid in (it["run_id"], it["gate2_run"], it["review_run"]):
        if rid:
            store.cancel(conn, rid)
    _set(conn, it["id"], state="dropped")
    if it["worktree"]:
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
        where = projects.repo_for(conn, goal) if goal is not None else repo()
        workspace.remove(where, it["id"], delete_branch=(it["commit_sha"] is None))
    return it["id"]


def retry(conn: sqlite3.Connection, iid: str) -> str:
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    if it["state"] in LIVE:
        raise ValueError(f"item {it['id']} is {it['state']}")
    fields: Dict[str, Any] = {"state": "waiting", "error": None}
    if it["worktree"] and not Path(it["worktree"]).exists():     # dropped: start afresh
        fields.update(worktree=None, branch=None, base=None, commit_sha=None, tries=0,
                      touched="[]", verdict=None, summary=None, review=None, reviews=0, review_run=None)
    _set(conn, it["id"], **fields)
    return it["id"]
