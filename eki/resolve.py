"""The side path of the queue: a change that can't be rebased mechanically
(docs/self-build.md, "The queue").

The queue calls `start` when an item's rebase onto its predicted head stops
on a conflict: a resolve run starts in the item's own worktree, mid-rebase,
told what the item is for, what the head changed in the conflicted files and
what each landed change was for (eki/landed.py).
`tick` moves on every item whose resolve or gate-1 run has ended: eki
continues the rebase, refuses a result with markers left or not on top of
the head, runs gate 1 again (a project item: its project's check, or none
when it has none), and puts the item at the back of the queue.

Everything is rebuilt from the items table and the runs; every git step is
safe to repeat, so a restart at any point loses nothing.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from typing import List, Optional, Tuple

from . import db, landed, projects, projectwork, rebase, resolvebrief, selfbrief, selfwork, store
from .workspace import WorkspaceError, git

#: resolve runs per item, in all, before it is left for a person
MAX_RUNS = 3
#: lines of the head's diff a resolver is shown
DIFF_LINES = 150


# ---- in -----------------------------------------------------------------------------------

def start(conn: sqlite3.Connection, it: sqlite3.Row, head: str, conflicted: List[str]) -> str:
    """A resolve run for `it`, stopped mid-rebase onto `head` on `conflicted`. Returns the run id."""
    changes = head_changes(it, head, conflicted)
    intents = landed.intents(conn, it, head, conflicted)
    with db.tx(conn):
        return _start(conn, it, head, conflicted, changes, intents)


def _start(conn: sqlite3.Connection, it: sqlite3.Row, head: str, conflicted: List[str],
           changes: str, intents: str, retry: Optional[Tuple[str, str]] = None) -> str:
    """`retry`: (what went wrong, the hunks still marked) — the second and last try."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    kw = dict(goal=goal["text"] if goal else "", title=it["title"], spec=it["spec"],
              summary=it["summary"] or "", head=head, conflicted=conflicted,
              head_changes=changes, landed=intents, project=projectwork.project_of(conn, goal))
    prompt = (resolvebrief.retry(previous_failure=retry[0], hunks=retry[1], **kw) if retry
              else resolvebrief.resolve(**kw))
    owner = goal["owner"] if goal else "eki"
    rid = store.create_run(conn, it["thread_id"], prompt, row="code",
                           priority="now" if owner == "you" else "background")
    selfwork._set(conn, it["id"], state="resolving", run_id=rid, head=head, gate2_run=None,
                  gate2=None, rebased=None, error=None)
    return rid


def head_changes(it: sqlite3.Row, head: str, files: List[str]) -> str:
    """What the head did to `files` since the item's base: the log, and a bounded diff."""
    wt = it["worktree"]
    since = it["base"] or head
    log = git(wt, "log", "--oneline", f"{since}..{head}", "--", *files, check=False)
    mine = it["commit_sha"] or it["base"] or head
    fork = git(wt, "merge-base", mine, head, check=False) or since
    diff = git(wt, "diff", f"{fork}..{head}", "--", *files, check=False).splitlines()
    if len(diff) > DIFF_LINES:
        diff = diff[:DIFF_LINES] + [f"… ({len(diff) - DIFF_LINES} more lines)"]
    return (f"$ git log --oneline {since[:12]}..{head[:12]} -- {' '.join(files)}\n"
            f"{log or '(no commits)'}\n\n"
            f"$ git diff {fork[:12]}..{head[:12]} -- {' '.join(files)}\n" + ("\n".join(diff) or "(no diff)"))


# ---- the tick -------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    for it in selfwork.items_in(conn, ("resolving",)):
        said += _conclude_resolve(conn, it)
    for it in selfwork.items_in(conn, ("rechecking",)):
        said += _conclude_recheck(conn, it)
    return said


def _still(conn: sqlite3.Connection, it: sqlite3.Row, state: str) -> bool:
    row = conn.execute("SELECT state, run_id FROM items WHERE id=?", (it["id"],)).fetchone()
    return row is not None and row["state"] == state and row["run_id"] == it["run_id"]


def _leave(conn: sqlite3.Connection, it: sqlite3.Row, why: str, person: bool = False, **fields) -> List[str]:
    """Stop the rebase and leave the item for a person, the branch as it was. `person`:
    the resolver asked for one, so a push says so."""
    wt = it["worktree"]
    try:
        rebase.abort(wt)
        if it["commit_sha"] and it["head"] and _is_ancestor(wt, it["head"], "HEAD") \
                and not _is_ancestor(wt, "HEAD", it["commit_sha"]):
            git(wt, "reset", "-q", "--hard", it["commit_sha"])    # a finished result refused
    except WorkspaceError as e:
        why = f"{why} (and {e})"
    with db.tx(conn):
        if not _still(conn, it, it["state"]):
            return []
        selfwork._set(conn, it["id"], state="left", error=why, **fields)
        if person:
            selfwork.needs_you(conn, it, "left", f"left for you: {why}", f"eki self show {it['id']}")
    return [f"item {it['id']}: left for you — {why}"]


def _is_ancestor(where: str, commit: str, of: str) -> bool:
    return subprocess.run(["git", "-C", str(where), "merge-base", "--is-ancestor", commit, of],
                          capture_output=True).returncode == 0


def resolve_runs(conn: sqlite3.Connection, it: sqlite3.Row) -> int:
    return sum(1 for r in store.thread_runs(conn, it["thread_id"])
               if (r["prompt"] or "").startswith(resolvebrief.FIRST_LINE))


def _conclude_resolve(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if run is not None and run["state"] in store.ACTIVE:
        return []
    if run is None or run["state"] != "done":
        state = run["state"] if run else "missing"
        return _leave(conn, it, f"the resolve run ended {state}: {(run['error'] if run else '') or ''}".strip())
    verdict, reason, summary = selfbrief.outcome(store.answer(conn, run["id"]))
    if verdict == "person":
        return _leave(conn, it, reason or "the resolver asked for a person", person=True)
    wt, head = it["worktree"], it["head"]
    try:
        left = rebase.markers(wt, head) if rebase.in_progress(wt) else []
        if left or not _has_item(store.answer(conn, run["id"])):
            return _failed(conn, it, run, left)
        stopped = rebase.proceed(wt)
    except WorkspaceError as e:
        return _leave(conn, it, f"the rebase would not go on: {e}")
    if stopped:
        if resolve_runs(conn, it) >= MAX_RUNS:
            return _leave(conn, it, f"still conflicted after {MAX_RUNS} resolve runs: {', '.join(stopped)}")
        changes = head_changes(it, head, stopped)
        intents = landed.intents(conn, it, head, stopped)
        with db.tx(conn):
            if not _still(conn, it, "resolving"):
                return []
            rid = _start(conn, it, head, stopped, changes, intents)
        return [f"item {it['id']}: stopped again on {', '.join(stopped)}; resolving ({rid[:8]})"]
    left = rebase.markers(wt, head)
    if left:
        return _leave(conn, it, f"conflict markers left in {', '.join(left)}")
    if not _is_ancestor(wt, head, "HEAD"):
        return _leave(conn, it, f"the result is not on top of the head {head[:12]}")
    sha = git(wt, "rev-parse", "HEAD")
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    project = projectwork.project_of(conn, goal)
    argv = projects.check_argv(project) if project is not None else selfwork.CHECK
    with db.tx(conn):
        if not _still(conn, it, "resolving"):
            return []
        if argv is None:                     # nothing to judge it with: as a green recheck
            selfwork._set(conn, it["id"], state="queued", queued_at=db.now(), head=it["head"],
                          rebased=sha, commit_sha=sha, verdict=projectwork.not_judged(project),
                          error=None)
            return [f"item {it['id']}: resolved; not judged; back in the queue"]
        rid = store.create_run(conn, it["thread_id"], argv, provider="command",
                               priority=run["priority"])
        selfwork._set(conn, it["id"], state="rechecking", run_id=rid, commit_sha=sha, error=None)
    return [f"item {it['id']}: resolved onto {head[:12]}; gate 1 again"]


def _has_item(answer: str) -> bool:
    """The answer has an `ITEM:` line (the regex of selfbrief.outcome)."""
    return re.search(r"^\s*`?ITEM:\s*(done|partial|person)\b", answer or "", re.M | re.I) is not None


def _failed(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row, left: List[str]) -> List[str]:
    """A resolver that left markers, or no ITEM line: one more run told what was wrong,
    or — when this was already that run (its prompt says so) — left for a person."""
    wt, head = it["worktree"], it["head"]
    what = f"conflict markers left in {', '.join(left)}" if left else "no ITEM line"
    if resolvebrief.RETRY_LINE in (run["prompt"] or ""):
        return _leave(conn, it, f"{what} after a second resolve run")
    try:
        files = left or rebase.conflicted(wt)
        hunks = rebase.marked_hunks(wt, left)
    except WorkspaceError as e:
        return _leave(conn, it, f"{what} (and {e})")
    if left:
        where = "; ".join(f"lines {rebase.marked_lines(wt, f)} of {f}" for f in left)
        failure = f"you described the fix but the file still has markers at {where}; edit the files"
    else:
        failure = "your answer had no ITEM: line; end with a SUMMARY: paragraph and an ITEM: line"
    changes = head_changes(it, head, files)
    intents = landed.intents(conn, it, head, files)
    with db.tx(conn):
        if not _still(conn, it, "resolving"):
            return []
        rid = _start(conn, it, head, files, changes, intents, retry=(failure, hunks))
    who = f"left markers in {', '.join(left)}" if left else "gave no ITEM line"
    return [f"item {it['id']}: resolver {who}; second try ({rid[:8]})"]


def _conclude_recheck(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if run is not None and run["state"] in store.ACTIVE:
        return []
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-25:]) if run else ""
    if run is None or run["state"] != "done":
        state = run["state"] if run else "missing"
        tail = (tail + f"\n(checks {state}: {(run['error'] if run else '') or ''})").strip()
    with db.tx(conn):
        if not _still(conn, it, "rechecking"):
            return []
        if run is not None and run["state"] == "done":
            # The branch now sits on the head it was resolved onto. Keep that head as the
            # item's base so the next rebase moves only the item's own commits: a plain
            # rebase would carry the predicted head's commits along, even ones whose
            # item has since failed gate 2 and left the queue.
            selfwork._set(conn, it["id"], state="queued", queued_at=db.now(),
                          head=it["head"], rebased=it["commit_sha"],
                          verdict=tail[-800:], error=None)
            return [f"item {it['id']}: resolved and green; back in the queue"]
        selfwork._set(conn, it["id"], state="unfit", verdict=tail[-800:],
                      error=f"checks {run['state'] if run else 'missing'} after resolving")
    return [f"item {it['id']}: unfit after resolving — left for you"]
