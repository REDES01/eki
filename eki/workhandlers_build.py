"""workrun.HANDLERS for plan, build, check and fix actions (docs/work-model.md):
start_run (plan, draft, build, fix, review — and the wake of a turn whose
card calls are back), run_gate 1 and ask_person.

Each does what its action says exactly once: it `claim`s its own
work_actions row in the same transaction as the run it starts, so a restart
between saving an action and doing it starts the run then, and never twice
(a run start is keyed by item, stage and tries: the action's own words). The
queue's roles and gates (resolve, gate 2) are eki/workhandlers_queue.py's
START and GATES, looked up when called.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import (callsback, callwait, db, doccheck, fixer, integration, notify, projectbrief, projects, projectwork,
               review, selfbrief, selfdraft, selfwork, store, workflow, workflowgates, workspace)
from .workmodel import Action, Event

Handler = Callable[[sqlite3.Connection, Any, Action], List[str]]


def claim(conn: sqlite3.Connection, action: Action) -> bool:
    """Under the caller's tx: mark the action's saved row done. False when it is done already."""
    saved = db.dumps({"kind": action.kind, "item": action.item, "data": action.data})
    row = conn.execute("SELECT id FROM work_actions WHERE done_at IS NULL AND item_id IS ? AND action=? "
                       "ORDER BY id LIMIT 1", (action.item, saved)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE work_actions SET done_at=? WHERE id=?", (db.now(), row["id"]))
    return True


def _item(conn: sqlite3.Connection, iid: Optional[str]) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone() if iid else None


def _goal(conn: sqlite3.Connection, gid: Optional[str]) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone() if gid else None


def _row(conn: sqlite3.Connection, project: Any) -> Optional[sqlite3.Row]:
    """The project's row; None for eki itself."""
    return None if project.kind == "eki" else projects.get(conn, project.id)


def _drop(conn: sqlite3.Connection, action: Action) -> List[str]:
    """Its item or goal is gone: the action is done with."""
    with db.tx(conn):
        claim(conn, action)
    return []


# ---- start_run ----------------------------------------------------------------------------

def start_run(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    it = _item(conn, action.item)
    if action.data.get("again") and it is not None and callwait.waits_of(it):
        return _wake(conn, project, action, it)
    role = action.data.get("role")
    fn = START.get(role) or _queue("START").get(role)
    if fn is None:
        raise LookupError(f"no start_run handler for role {role!r}")
    return fn(conn, project, action)


def _queue(name: str) -> Dict[Any, Handler]:
    from . import workhandlers_queue   # its own module: looked up when called
    return getattr(workhandlers_queue, name, {})


def _base(conn: sqlite3.Connection, project: Any) -> str:
    row = _row(conn, project)
    return integration.sync() if row is None else projects.base(row)


def _plan(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    g = _goal(conn, action.data.get("goal"))
    if g is None:
        return _drop(conn, action)
    base = _base(conn, project)
    with db.tx(conn):
        if not claim(conn, action):
            return []
        if action.data.get("role") == "draft":
            selfdraft.start_draft(conn, g["id"], g["wish"] or g["text"], base, g["owner"])
        else:
            selfdraft.start_plan(conn, g["id"], g["text"], base, g["owner"])
    return []


def _build(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """A build try: its worktree (made once), the brief, the run."""
    it = _item(conn, action.item)
    goal = _goal(conn, it["goal_id"]) if it is not None else None
    if goal is None:
        return _drop(conn, action)
    row = _row(conn, project)
    others = _others(conn, it, project)
    with db.tx(conn):
        if not claim(conn, action):
            return []
        if not it["worktree"]:
            if row is None:
                base, branch = integration.sync(), f"self/{it['id']}"
                wt = workspace.add(project.repo, it["id"], base=base, branch=branch)
            else:
                wt, base, branch = projectwork.worktree(row, it["id"])
            tid = store.create_thread(conn, f"{row['name'] if row else 'self'}: {it['title']}", str(wt), by="eki")
            selfwork._set(conn, it["id"], worktree=str(wt), branch=branch, base=base, thread_id=tid)
            it = _item(conn, it["id"])
        failure = it["verdict"] if it["tries"] > 1 and _gate1(it) != "green" else None   # not a review's rebuild
        brief = dict(goal=goal["text"], title=it["title"], spec=it["spec"], files=json.loads(it["files"] or "[]"),
                     branch=it["branch"], base=it["base"], source=str(selfwork.source()), others=others,
                     failure=failure, objection=review.objection(it))
        prompt = selfbrief.build(**brief) if row is None else projectbrief.build(**brief, project=row)
        prompt += callsback.brief_lines(conn, callsback.unread(conn, it))
        rid = store.create_run(conn, it["thread_id"], prompt, row="code",
                               priority="now" if goal["owner"] == "you" else "background")
        selfwork._set(conn, it["id"], run_id=rid, error=None)
    return []


def _gate1(it: sqlite3.Row) -> Optional[str]:
    try:
        return (json.loads(it["work"] or "null") or {}).get("gate1")
    except (ValueError, AttributeError):
        return None


def _others(conn: sqlite3.Connection, it: sqlite3.Row, project: Any) -> List[Tuple[str, List[str]]]:
    """The items with a run going in the same repo: the brief tells the builder to stay out of their files."""
    repos: Dict[str, Optional[str]] = {}
    out = []
    for x in selfwork.items_in(conn, selfwork.LIVE):
        if x["id"] == it["id"]:
            continue
        if x["goal_id"] not in repos:
            try:
                repos[x["goal_id"]] = str(projects.repo_for(conn, _goal(conn, x["goal_id"])))
            except (KeyError, TypeError):
                repos[x["goal_id"]] = None
        if repos[x["goal_id"]] == str(project.repo):
            out.append((x["title"], json.loads(x["files"] or "[]")))
    return out


def _wake(conn: sqlite3.Connection, project: Any, action: Action, it: sqlite3.Row) -> List[str]:
    """The next turn of a build or resolve whose card calls are back (eki/callwait.py)."""
    with db.tx(conn):
        if claim(conn, action):
            callwait.wake(conn, it)
    return []


def _fix(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """The item's one fixer run; left for a person when git won't put it on main."""
    from . import buildfeed
    it = _item(conn, action.item)
    if it is None:
        return _drop(conn, action)
    stage, failure = action.data.get("stage"), action.data.get("failure") or ""
    if action.data.get("again"):           # its run was lost: the same fixer run, told the same failure
        note = fixer.of(it) or {}
        stage, failure = note.get("stage"), note.get("failure") or failure
    try:
        got = fixer.prepare(conn, it)
    except (workspace.WorkspaceError, OSError, integration.IntegrationError) as e:
        why = f"the fixer couldn't start: {e}"
        if stage in QUEUE_STAGES:          # the queue's: left with what called it, as before the work model
            why = (failure.strip().splitlines() or [f"{stage} failed"])[-1]
        return buildfeed.feed(conn, it, Event("item_finished", db.now(), {"leave": why}),
                              then=lambda c: claim(c, action) and None, error=why)
    with db.tx(conn):
        if not claim(conn, action):
            return []
        rid = fixer.launch(conn, it, stage if stage in fixer.STAGES else "gate1", failure, got)
    if stage in QUEUE_STAGES:              # the queue's board says the run, not the state (workhandlers_queue.say)
        return [f"item {it['id']}: {stage} failed — the fixer is on it ({rid[:8]})"]
    return []


def _review(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """A judge run beside gate 1; `none: <why>` when no judge can run."""
    it = _item(conn, action.item)
    if it is None:
        return _drop(conn, action)
    if action.data.get("second"):
        return _second(conn, action, it)
    with db.tx(conn):
        if claim(conn, action):
            rev, why = review.ask(conn, it)
            selfwork._set(conn, it["id"], review_run=rev, review=None if rev else f"none: {why}")
    return []


def _second(conn: sqlite3.Connection, action: Action, it: sqlite3.Row) -> List[str]:
    """A second judge on another backend for the uncertain checks (judge.second); with none to
    start, the item's word is fed again as settled, and the model proposes it for a person."""
    from . import buildfeed, judge
    with db.tx(conn):
        if not claim(conn, action):
            return []
        rid = judge.second(conn, it)
    if rid is not None:
        return [f"item {it['id']}: a second judge looks ({rid[:8]})"]
    return buildfeed.verdict(conn, _item(conn, it["id"]), {"result": "green", "ended": "done", "second": True})


#: the fixer stages the queue calls it for (gate 2, a resolve): their words are the handler's
QUEUE_STAGES = ("gate2", "resolve")
START: Dict[str, Handler] = {"plan": _plan, "draft": _plan, "build": _build, "fix": _fix, "review": _review}


# ---- run_gate 1 ---------------------------------------------------------------------------

def run_gate(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    fn = GATES.get(action.data.get("gate")) or _queue("GATES").get(action.data.get("gate"))
    if fn is None:
        raise LookupError(f"no run_gate handler for gate {action.data.get('gate')!r}")
    return fn(conn, project, action)


def _gate_one(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """Gate 1 as a command run: eki's bin/check (the doccheck for a docs-only change), a project's
    card's gates, else its check (docs/work-model.md, difference e)."""
    it = _item(conn, action.item)
    if it is None:
        return _drop(conn, action)
    from . import judge
    row = _row(conn, project)
    argv, judged_by = command(conn, it, row)
    was = store.run(conn, it["run_id"]) if it["run_id"] else None
    goal = _goal(conn, it["goal_id"])
    priority = was["priority"] if was is not None else ("now" if goal and goal["owner"] == "you" else "background")
    with db.tx(conn):
        if claim(conn, action):
            rid = store.create_run(conn, it["thread_id"], argv, provider="command", priority=priority)
            selfwork._set(conn, it["id"], run_id=rid, **({"judged_by": judged_by} if judged_by is not None else {}))
            if row is not None:            # a playtester card's gate 1 is a `play` judge run (eki/judge.py)
                judge.play(conn, _item(conn, it["id"]), workflow.of_project(conn, row))
    return []


def command(conn: sqlite3.Connection, it: sqlite3.Row, row: Optional[sqlite3.Row]) -> Tuple[str, Optional[str]]:
    """(gate 1's argv JSON, what it runs — None for eki's own)."""
    touched = json.loads(it["touched"] or "[]")
    if row is None:
        docs = doccheck.docs_only(touched)
        return (doccheck.command(it["base"], it["commit_sha"]) if docs else selfwork.CHECK), None
    wf = workflow.of_project(conn, row)
    if wf is not None:
        got = workflowgates.gate(wf, it["title"], touched)
        return got if got is not None else (json.dumps(["/bin/sh", "-c", "true", "eki-judge"]), workflow.said(wf))
    judge = projects.check_argv(row)
    if judge is None:                  # its check went away since: nothing to judge with
        return json.dumps(["/bin/sh", "-c", "true", "eki-judge"]), projectwork.not_judged(row)
    return judge, judged_by(row, judge)


def judged_by(project: sqlite3.Row, judge: str) -> str:
    """The check's command and where it came from: `make x (set)`, `npm run test (guessed: …)`."""
    cmd = json.loads(judge)[2]
    source = project["check_from"] if "check_from" in project.keys() else None
    if not source:
        return cmd
    return f"{cmd} ({source})" if source == "set" or source.startswith("guessed: ") else cmd


GATES: Dict[Any, Handler] = {1: _gate_one}


# ---- ask_person ---------------------------------------------------------------------------

def ask_person(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """A push to the person's phone, once per key: the item needs their hands."""
    it = _item(conn, action.item)
    what = action.data.get("what") or f"left for you: {action.why}"
    body = " ".join(f"{it['title'] if it else action.item} — {what}: "
                    f"eki self show {action.item}".split())[:200]
    notify.queue(conn, action.data.get("key") or f"item:{action.item}:ask", "needs_you", "eki · needs you", body)
    return [f"item {action.item}: asks you — {action.why}"]


HANDLERS: Dict[str, Handler] = {"start_run": start_run, "run_gate": run_gate, "ask_person": ask_person}
