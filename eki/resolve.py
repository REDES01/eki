"""The side path of the queue: a change that can't be rebased mechanically
(docs/self-build.md, "The queue").

The queue calls `start` when an item's rebase onto its predicted head stops
on a conflict: a resolve run starts in the item's own worktree, mid-rebase,
told what the item is for, what the head changed in the conflicted files and
what each landed change was for (eki/landed.py).
`tick` looks at every item whose resolve run has ended: eki continues the
rebase, finds markers left or a result not on top of the head, and feeds
what it found; eki/workstep_queue.py `resolved` says what comes of it (back
of the queue on that head, one more run, the fixer or a person). Gate 1 does not run
again: gate 2 judges the item with the changes under it, and its bisect says
whose fault a red is. `resolved_on` keeps the head it was resolved onto, so a
later re-stack (queue._rebase) moves only the item's own commits. An item
left in `rechecking` by an older build goes back to the queue once its run
ends, its result not counted.

Everything is rebuilt from the items table and the runs; every git step is
safe to repeat, so a restart at any point loses nothing.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from typing import Any, List, Optional, Tuple

from . import callwait, db, landed, projectwork, rebase, resolvebrief, selfbrief, selfwork, store
from .workhandlers_queue import can_fix, event, left_error, say
from .workmodel import Action, Event, ItemState
from .workspace import WorkspaceError, git

#: resolve runs per item, in all, before it is left for a person
MAX_RUNS = 3
#: lines of the head's diff a resolver is shown
DIFF_LINES = 150


# ---- in -----------------------------------------------------------------------------------

def start(conn: sqlite3.Connection, it: sqlite3.Row, head: str, conflicted: List[str]) -> str:
    """`it` stopped mid-rebase onto `head` on `conflicted`: fed as a conflict, its resolve run
    started. Returns the run id ('' when the item had moved on)."""
    def then(c: sqlite3.Connection, old: Optional[ItemState], new: ItemState) -> None:
        if new.stage == "resolving":
            selfwork._set(c, it["id"], head=head, rebased=None, gate2_run=None, gate2_on=None)

    say(conn, it["goal_id"], it["id"], event("merge_result", result="conflict", files=list(conflicted),
                                             on=head[:12], runs=resolve_runs(conn, it)), then=then)
    cur = selfwork.store_item(conn, it["id"])
    return cur["run_id"] if cur["state"] == "resolving" and cur["head"] == head else ""


def begin(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """The start_run(resolve) handler: the item's next resolve run, once (its count is the key)."""
    it = selfwork.store_item(conn, action.item)
    d = action.data
    if it is None or it["state"] != "resolving" or resolve_runs(conn, it) >= int(d.get("runs") or 1):
        return []
    head, files, retry = it["head"], list(d.get("files") or []), None
    try:
        if d.get("retry"):
            retry = _retry(it, head, files)
        changes = head_changes(it, head, files)
    except WorkspaceError as e:
        why = f"{d.get('retry') or 'the resolve'} (and {e})"
        return say(conn, it["goal_id"], it["id"], event("item_failed", kind="judgment", why=why), carry=False,
                   after=left_error)
    intents = landed.intents(conn, it, head, files)
    with db.tx(conn):
        cur = selfwork.store_item(conn, it["id"])
        if cur["state"] != "resolving" or resolve_runs(conn, cur) >= int(d.get("runs") or 1):
            return []
        rid = _start(conn, cur, head, files, changes, intents, retry=retry)
    return [f"item {it['id']}: {action.why} ({rid[:8]})"]


def _retry(it: sqlite3.Row, head: str, files: List[str]) -> Tuple[str, str]:
    """(what went wrong, the hunks still marked) for the second try."""
    wt = it["worktree"]
    left = rebase.markers(wt, head) if rebase.in_progress(wt) else []
    hunks = rebase.marked_hunks(wt, left)
    if left:
        where = "; ".join(f"lines {rebase.marked_lines(wt, f)} of {f}" for f in left)
        return f"you described the fix but the file still has markers at {where}; edit the files", hunks
    return "your answer had no ITEM: line; end with a SUMMARY: paragraph and an ITEM: line", hunks


def _start(conn: sqlite3.Connection, it: sqlite3.Row, head: str, conflicted: List[str],
           changes: str, intents: str, retry: Optional[Tuple[str, str]] = None) -> str:
    """Under the caller's tx: the resolve run itself. `retry`: (what went wrong, the hunks still
    marked) — the second and last try."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    kw = dict(goal=goal["text"] if goal else "", title=it["title"], spec=it["spec"],
              summary=it["summary"] or "", head=head, conflicted=conflicted,
              head_changes=changes, landed=intents, project=projectwork.project_of(conn, goal))
    prompt = (resolvebrief.retry(previous_failure=retry[0], hunks=retry[1], **kw) if retry
              else resolvebrief.resolve(**kw))
    owner = goal["owner"] if goal else "eki"
    rid = store.create_run(conn, it["thread_id"], prompt, row="code", fresh=True,
                           priority="now" if owner == "you" else "background")
    selfwork._set(conn, it["id"], run_id=rid, head=head, resolved_on=head,
                  gate2_run=None, rebased=None, error=None)
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


def _ended(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[sqlite3.Row]:
    """The item's run, once it has ended (a missing one has); None while it runs."""
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if run is not None and run["state"] in store.ACTIVE:
        return None
    return run if run is not None else {"id": None, "state": "missing", "error": "", "prompt": ""}


def _feed(conn: sqlite3.Connection, it: sqlite3.Row, **facts: Any) -> List[str]:
    """What the resolve came to, fed. When it leaves the side path for the fixer or a person,
    the rebase is stopped first and the branch is as it was."""
    from . import workrun, workstep
    ev = event("item_finished", **{k: v for k, v in facts.items() if k not in ("waits", "columns", "sha")})
    goal, items, settings = workrun.load(conn, it["goal_id"])
    _, new, _ = workstep.step(goal, tuple(items), settings, Event(ev.kind, ev.at, {**ev.data, "item": it["id"]}))
    if any(x.id == it["id"] and x.stage in ("left", "fixing") for x in new):
        _abort(it)
    return say(conn, it["goal_id"], it["id"], ev, then=lambda c, old, new: _then(c, it, new, facts),
               after=left_error)


def _then(conn: sqlite3.Connection, it: sqlite3.Row, new: ItemState, facts: dict) -> None:
    if new.stage == "queued" and facts.get("sha"):
        selfwork._set(conn, it["id"], head=it["head"], rebased=facts["sha"], commit_sha=facts["sha"],
                      gate2_run=None, gate2_on=None, error=None, **facts.get("columns", {}))
    elif new.stage == "waiting on calls":
        conn.execute("UPDATE items SET waits=? WHERE id=?", (db.dumps(facts["waits"]), it["id"]))


def _abort(it: sqlite3.Row) -> None:
    """Stop the rebase, the branch as it was (a finished result refused)."""
    wt = it["worktree"]
    try:
        rebase.abort(wt)
        if it["commit_sha"] and it["head"] and _is_ancestor(wt, it["head"], "HEAD") \
                and not _is_ancestor(wt, "HEAD", it["commit_sha"]):
            git(wt, "reset", "-q", "--hard", it["commit_sha"])
    except WorkspaceError:
        pass


def _is_ancestor(where: str, commit: str, of: str) -> bool:
    return subprocess.run(["git", "-C", str(where), "merge-base", "--is-ancestor", commit, of],
                          capture_output=True).returncode == 0


def resolve_runs(conn: sqlite3.Connection, it: sqlite3.Row) -> int:
    return sum(1 for r in store.thread_runs(conn, it["thread_id"])
               if (r["prompt"] or "").startswith(resolvebrief.FIRST_LINE))


def _conclude_resolve(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = _ended(conn, it)
    if run is None:
        return []
    fixable = can_fix(conn, it)
    if run["state"] != "done":
        from . import buildfeed
        if (held := buildfeed.held(run)) is not None:     # a broken setup: held till it is fixed
            _abort(it)
            return buildfeed.feed(conn, it, held)
        return _feed(conn, it, ended=run["state"], error=run["error"] or "", can_fix=fixable)
    calls = callwait.outstanding(conn, run["id"])
    if calls and callwait.wakes(conn, it) < callwait.MAX_WAITS:
        waits = {"stage": "resolving", "run": run["id"], "since": db.now(), "calls": [c["id"] for c in calls]}
        cards = ", ".join(f"{callwait.card(c)} ({c['id'][:8]})" for c in calls)
        return _feed(conn, it, calls=len(calls), wakes=callwait.wakes(conn, it), cards=cards, waits=waits)
    with db.tx(conn):
        stopped = callwait.stopped(conn, it, run)
    return stopped + _feed(conn, it, can_fix=fixable, **_outcome(conn, it, run))


def _outcome(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row) -> dict:
    """What a resolve run that ended done came to: the rebase goes on, or what's wrong."""
    answer = store.answer(conn, run["id"])
    verdict, reason, summary = selfbrief.outcome(answer)
    if verdict == "person":
        return dict(person=True, why=reason or "the resolver asked for a person")
    wt, head = it["worktree"], it["head"]
    try:
        left = rebase.markers(wt, head) if rebase.in_progress(wt) else []
        if left or not _has_item(answer):
            what = f"conflict markers left in {', '.join(left)}" if left else "no ITEM line"
            who = f"left markers in {', '.join(left)}" if left else "gave no ITEM line"
            try:
                files = left or rebase.conflicted(wt)
            except WorkspaceError as e:
                return dict(outcome="broken", why=f"{what} (and {e})")
            return dict(outcome="bad", why=what, who=who, files=files, runs=resolve_runs(conn, it),
                        retried=resolvebrief.RETRY_LINE in (run["prompt"] or ""))
        stopped = rebase.proceed(wt)
    except WorkspaceError as e:
        return dict(outcome="broken", why=f"the rebase would not go on: {e}")
    if stopped:
        return dict(outcome="stopped", files=stopped, runs=resolve_runs(conn, it))
    left = rebase.markers(wt, head)
    if left:
        return dict(outcome="broken", why=f"conflict markers left in {', '.join(left)}")
    if not _is_ancestor(wt, head, "HEAD"):
        return dict(outcome="broken", why=f"the result is not on top of the head {head[:12]}")
    return dict(outcome="resolved", on=head[:12], sha=git(wt, "rev-parse", "HEAD"))


def _has_item(answer: str) -> bool:
    """The answer has an `ITEM:` line (the regex of selfbrief.outcome)."""
    return re.search(r"^\s*`?ITEM:\s*(done|partial|person)\b", answer or "", re.M | re.I) is not None


def _conclude_recheck(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """An item left in `rechecking` by an older build (gate 1 after a resolve): once its run
    has ended, whatever it said, it goes back to the queue for gate 2. Nothing enters
    `rechecking` any more."""
    run = _ended(conn, it)
    if run is None:
        return []
    verdict = f"resolved; the old gate-1 check ({run['state']}) is not counted — gate 2 judges it"
    return _feed(conn, it, ended="done", sha=it["commit_sha"], columns={"verdict": verdict})
