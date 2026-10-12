"""The stack of a queue, eki's or a project's: the git and run plumbing of the
walk (docs/self-build.md, "The queue"; the rules are eki/workstep_queue.py).

`walk` rebases each queued item onto its predicted head — the target's tip
plus the items ahead of it — and feeds what came of it (`merge_result`);
then feeds the stack's gate-2 results, front first (`check_verdict`), and
step says what lands, what is checked next as one batch, what waits and
what is the bad one. `start_gate2` is the run_gate 2 handler's plumbing: one
check on the batch's top, in a fresh gate tree.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from . import db, rebase, resolve, selfwork, store, workhandlers_queue
from .workhandlers_queue import event, say
from .workmodel import Action, ItemState
from .workspace import WorkspaceError, git


@dataclass(frozen=True)
class Queue:
    rows: Callable[[], List[sqlite3.Row]]          # the queued items, front first
    tip: Callable[[], str]                         # the target's tip
    sync: Optional[Callable[[], Any]] = None       # fetch before a rebase (eki)
    unbatched: Tuple[str, str] = ("", "")          # (line, verdict) when nothing is checked together
    batched: bool = True                           # the project's gates hold batch


# ---- the walk -------------------------------------------------------------------------------

def walk(conn: sqlite3.Connection, q: Queue, again: bool = True) -> List[str]:
    items = q.rows()
    if not items:
        return []
    if q.sync is not None and _needs_rebase(items, q.tip()):
        q.sync()
    head = q.tip()
    said: List[str] = []
    stack: List[sqlite3.Row] = []
    for it in q.rows():
        if it["head"] != head or not it["rebased"] or (not q.batched and it["gate2"] != "green"):
            sha, lines = rebase_one(conn, q, it, head)     # a resolved one is on its head: said so
            said += lines
            if sha is None:
                continue                           # out of the stack: head doesn't advance past it
            it = selfwork.store_item(conn, it["id"])
            if it["state"] != "queued":
                head = it["rebased"] or head       # landed at once (nothing checked together)
                continue
        stack.append(it)
        head = it["rebased"]
    said += judge(conn, stack)
    if again and len(q.rows()) < len(stack):
        said += walk(conn, q, again=False)         # some left the queue: those behind them move up now
    return said


def _needs_rebase(items: List[sqlite3.Row], main: str) -> bool:
    head = main
    for it in items:
        if it["head"] != head or not it["rebased"]:
            return True
        head = it["rebased"]
    return False


def rebase_one(conn: sqlite3.Connection, q: Queue, it: sqlite3.Row, head: str) -> Tuple[Optional[str], List[str]]:
    """(the rebased sha or None, lines). None: it left the stack."""
    if it["gate2_run"]:
        _cancel(conn, it["gate2_run"])
    wt = it["worktree"]
    try:
        at = git(wt, "rev-parse", "HEAD")
        since = it["head"] if it["rebased"] and at == it["rebased"] else None
        if since is None and it["resolved_on"] and resolve._is_ancestor(wt, it["resolved_on"], at):
            since = it["resolved_on"]              # moved since its resolve: still only its own commits
        conflicts = rebase.onto(wt, head, since=since)
    except (WorkspaceError, OSError) as e:
        return None, say(conn, it["goal_id"], it["id"], event("merge_result", result="failed", error=str(e)),
                         after=lambda c, old, new: workhandlers_queue.stopped_error(
                             c, new.id, "left", f"the rebase onto {head[:12]} failed: {e}"))
    gone = _departed(conn, it, since, head) if since and since != head else []
    without = f" without {', '.join(gone)} (left the queue)" if gone else ""
    if conflicts:
        rid = resolve.start(conn, it, head, conflicts)
        if not rid:
            return None, []
        return None, [f"item {it['id']}: conflict in {', '.join(conflicts)}"
                      f"{', re-stacked' + without if gone else ''} → resolving ({rid[:8]})"]
    sha = git(wt, "rev-parse", "HEAD")
    line = f"re-stacked on {head[:12]}{without}" if gone else f"rebased on {head[:12]}"

    def then(c: sqlite3.Connection, old: Optional[ItemState], new: ItemState) -> None:
        fields = dict(head=head, rebased=sha, gate2_run=None, gate2_on=None)
        if it["resolved_on"]:
            fields["resolved_on"] = head
        if new.gate2 == "green" and q.unbatched[1]:
            fields["verdict"] = q.unbatched[1]
        selfwork._set(c, it["id"], **fields)

    said = say(conn, it["goal_id"], it["id"], event("merge_result", result="rebased", on=head[:12], line=line,
                                                    unbatched=q.unbatched[0]), then=then)
    return (sha if selfwork.store_item(conn, it["id"])["rebased"] == sha else None), said


def _departed(conn: sqlite3.Connection, it: sqlite3.Row, since: str, head: str) -> List[str]:
    """Items under `since` but not under `head`, out of the queue: what a re-stack left behind."""
    left_behind = set(git(it["worktree"], "rev-list", since, f"^{head}", check=False).split())
    rows = conn.execute("SELECT id, rebased FROM items WHERE id<>? AND rebased IS NOT NULL "
                        "AND state NOT IN ('queued', 'landed') ORDER BY id", (it["id"],)).fetchall()
    return [r["id"] for r in rows if r["rebased"] in left_behind]


def _cancel(conn: sqlite3.Connection, rid: str) -> None:
    run = store.run(conn, rid)
    if run is not None and run["state"] in store.ACTIVE:
        store.cancel(conn, rid)


# ---- gate 2 ---------------------------------------------------------------------------------

def result(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[str]:
    """'green', 'red', 'running' or None: gate 2 on this item's current rebased commit."""
    if not it["gate2_run"] or it["gate2_on"] != it["rebased"]:
        return None
    run = store.run(conn, it["gate2_run"])
    if run is None:
        return None
    if run["state"] in store.ACTIVE:
        return "running"
    return "green" if run["state"] == "done" else "red"


def _tail(conn: sqlite3.Connection, it: sqlite3.Row) -> str:
    run = store.run(conn, it["gate2_run"])
    tail = selfwork.failure_tail(store.answer(conn, run["id"]))
    return (tail + f"\n(gate 2 {run['state']}: {run['error'] or ''})").strip()


def judge(conn: sqlite3.Connection, stack: List[sqlite3.Row]) -> List[str]:
    """The stack's gate-2 results, fed to each goal in it: each decides for its own items."""
    if not stack:
        return []
    results = [result(conn, it) for it in stack]
    tails = {it["id"]: _tail(conn, it) for it, r in zip(stack, results) if r == "red"}
    data = dict(gate=2, stack=[{"id": it["id"], "result": r} for it, r in zip(stack, results)], tails=tails,
                can_fix=workhandlers_queue.can_fix(conn, stack[0]))

    def after(c: sqlite3.Connection, old: Optional[ItemState], new: ItemState) -> None:
        if new.stage == "unfit":
            workhandlers_queue.stopped_error(c, new.id, "unfit", "gate 2 failed",
                                             verdict=tails.get(new.id, "")[-selfwork.FAILURE_CHARS:])

    said: List[str] = []
    for gid in dict.fromkeys(it["goal_id"] for it in stack):
        said += say(conn, gid, None, event("check_verdict", **data), after=after)
    return said


def start_gate2(conn: sqlite3.Connection, project: Any, action: Action) -> List[str]:
    """The run_gate 2 handler: one check on the batch's top, judging the batch together.
    Done already when the top has a check on its current commit."""
    from . import queue
    ids = list(action.data.get("batch") or [action.item])
    batch = [selfwork.store_item(conn, i) for i in ids]
    it = batch[-1] if batch else None
    if it is None or any(b is None for b in batch) or it["state"] != "queued" or not it["rebased"]:
        return []
    if it["gate2_run"] and it["gate2_on"] == it["rebased"]:
        return []
    argv, docs = queue.gate2_command(conn, project, batch)
    if argv is None:
        return []
    tree = rebase.gate_tree(project.repo, f"gate2-{it['id']}", it["rebased"])
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
        selfwork._set(conn, it["id"], gate2_run=rid, gate2_on=it["rebased"])
    what = f"item {it['id']}" if len(batch) == 1 else f"a batch of {len(batch)} ({batch[0]['id']}..{it['id']})"
    where = "" if project.kind == "eki" else f" of {project.name}"
    return [f"gate 2 on {what}{where} at {it['rebased'][:12]} ({rid[:8]}){', docs only' if docs else ''}"]
