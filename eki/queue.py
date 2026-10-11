"""The queue: speculative rebase, gate 2 and landing (docs/self-build.md,
"The queue" and "Judging").

Queued items stand in order (queued_at, then created_at). Each one's
predicted head is integration main plus the rebased commits of the items
ahead of it — a stack. A conflict sends the item to the side path
(resolve.py) and the items behind it are rebased without it.

Gate 2 checks the stack as one batch: one run on the tip, which carries every
item under it. Green lands the whole batch. Red bisects: the middle of the
unjudged part is checked next, so one bad item is found in log2(n) runs and
the good ones before it land; the bad one is unfit and the items behind it
are rebased without it and checked again. One check at a time: items that
join while it runs wait for the next batch, they don't restart it. The front
lands — a fast-forward of integration main — once gate 2 is green on it or
on an item above it in the stack.

Gate 2 is the fast tests only (bin/check with EKI_CHECK_FAST=1: no drills);
the drills and the candidate checks run on the train, before the swap. A
docs-only item (doccheck.py) is judged by the doccheck instead: its files
parse as UTF-8 text and nothing outside the docs lane changed.

Nothing is kept in memory: each tick rebuilds what to do from the items
table and the runs, and every step is a run or an idempotent git operation,
so a restart anywhere loses nothing.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import db, doccheck, integration, locks, paths, rebase, resolve, selfwork, store, workspace
from .workspace import WorkspaceError, git

log = logging.getLogger("eki.queue")

#: gate 2, run in a fresh detached worktree of the predicted head plus the item
GATE2 = '["/bin/sh", "-c", "EKI_PYTHON=$PWD/.venv/bin/python EKI_CHECK_FAST=1 exec bin/check -q"]'
MODES = ("apply", "propose")
#: states that give the queue something to do
BUSY = ("proposed", "locked", "queued", "resolving", "rechecking")


# ---- asked for by hand ----------------------------------------------------------------------

def apply(conn: sqlite3.Connection, iid: str, *, yes: bool = False) -> str:
    """Queue one item: a proposed one, or a locked one with the person's yes."""
    it = selfwork.store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    if it["state"] == "locked" and not yes:
        files = ", ".join(json.loads(it["locked"] or "[]")) or "?"
        raise ValueError(f"item {it['id']} touches locked files: {files}, needs --yes")
    if it["state"] not in ("proposed", "locked"):
        raise ValueError(f"item {it['id']} is {it['state']}, not proposed")
    with db.tx(conn):
        selfwork._set(conn, it["id"], state="queued", queued_at=db.now(), head=None, rebased=None,
                      gate2=None, gate2_run=None, gate2_on=None, error=None)
    return it["id"]


def set_autonomy(mode: str) -> None:
    """'apply' queues fit items by itself, 'propose' waits for you: eki's workflow card's merge
    when it has one (auto / person), and self.autonomy in routing.json, which settings() reads."""
    if mode not in MODES:
        raise ValueError(f"autonomy is one of {', '.join(MODES)}, not {mode!r}")
    card = _own_card()
    if card is not None:
        from . import api_cardedit
        api_cardedit.save_settings(card, {"merge": "auto" if mode == "apply" else "person"})
    path = paths.config("routing")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data["self"] = {**(data.get("self") or {}), "autonomy": mode}
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _own_card() -> Optional[Path]:
    """eki's workflow card folder, when its source is named: the launcher's EKI_SOURCE, or the record
    of a build in this home's builds/. Anything else — a test (its own home, EKI_SOURCE cleared), a
    bare checkout, a build folder run under another home — never rewrites the committed card."""
    from . import workflow
    here = _here()
    built = (here / ".eki-build.json").exists() and here.parent == (paths.home() / "builds").resolve()
    if not os.environ.get("EKI_SOURCE") and not built:
        return None
    wf = workflow.of_project(None, None)
    return Path(wf.card) if wf is not None else None


def _here() -> Path:
    """The folder this code runs from."""
    return Path(__file__).resolve().parent.parent


def order(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """eki's own queued items, front first: a project's items stand in eki/projectqueue.py."""
    return conn.execute("SELECT i.* FROM items i LEFT JOIN goals g ON g.id=i.goal_id "
                        "WHERE i.state='queued' AND g.project IS NULL "
                        "ORDER BY COALESCE(i.queued_at, i.created_at), i.created_at").fetchall()


# ---- the tick --------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    if not selfwork.items_in(conn, BUSY) and not _ahead():
        return []                                  # nothing to do: git and the home stay untouched
    said = _admit(conn)
    said += resolve.tick(conn)
    said += _walk(conn)
    said += _land(conn)
    return said


def _ahead() -> bool:
    """Integration main has commits origin doesn't — without making the repo."""
    if not (paths.home() / "self" / "repo" / ".git").exists():
        return False
    try:
        there = git(integration.repo(), "rev-parse", "--verify", "-q", "origin/main", check=False)
        return there != integration.main() and (not there or integration.contains(there, "main"))
    except WorkspaceError:
        return False


def _set_if(conn: sqlite3.Connection, it: sqlite3.Row, was: str,
            _then: Optional[Callable[[], Any]] = None, **fields: Any) -> bool:
    """Write fields when the item is still in state `was` (a restart or a person may have moved it);
    `_then` runs in the same transaction, only when they were written."""
    with db.tx(conn):
        row = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
        if row is None or row["state"] != was:
            return False
        selfwork._set(conn, it["id"], **fields)
        if _then is not None:
            _then()
    return True


def _admit(conn: sqlite3.Connection) -> List[str]:
    waiting = selfwork.items_in(conn, ("proposed", "locked"))
    if not waiting:
        return []
    repo = integration.repo()
    said = []
    for it in waiting:
        if it["commit_sha"] and integration.contains(it["commit_sha"], "main"):
            if _set_if(conn, it, it["state"], state="landed", landed_at=db.now(), gate2="green"):
                said.append(f"item {it['id']}: landed — {it['commit_sha'][:12]} is in main")
    apply_mode = selfwork.settings().get("autonomy") == "apply"
    for it in selfwork.items_in(conn, ("proposed",)):
        if not it["worktree"] or not workspace.belongs(it["worktree"], repo):
            continue                               # built before the queue: merged by hand
        files = json.loads(it["touched"] or "[]") + json.loads(it["files"] or "[]")
        held = locks.locked_in(files)
        if held:
            if _set_if(conn, it, "proposed", state="locked", locked=db.dumps(held),
                       _then=lambda: selfwork.needs_you(conn, it, "locked", f"locked, touches {', '.join(held)}",
                                                        f"eki self apply {it['id']} --yes")):
                said.append(f"item {it['id']}: locked — touches {', '.join(held)}; needs a person")
        elif apply_mode and not (it["review"] or "").startswith("no:"):   # objected twice: a person's call
            if _set_if(conn, it, "proposed", state="queued", queued_at=db.now(), head=None,
                       rebased=None, gate2=None, gate2_run=None, gate2_on=None):
                said.append(f"item {it['id']}: queued")
    return said


def _needs_rebase(items: List[sqlite3.Row], main: str) -> bool:
    head = main
    for it in items:
        if it["head"] != head or not it["rebased"]:
            return True
        head = it["rebased"]
    return False


def _walk(conn: sqlite3.Connection, again: bool = True) -> List[str]:
    items = order(conn)
    if not items:
        return []
    if _needs_rebase(items, integration.main()):
        integration.sync()                         # eki fetches before every rebase
    head = integration.main()
    said: List[str] = []
    stack: List[sqlite3.Row] = []
    for it in order(conn):
        if it["head"] != head or not it["rebased"]:
            moved = _rebase(conn, it, head)
            said += moved[1]
            if moved[0] is None:
                continue                           # out of the queue: head doesn't advance past it
            it = selfwork.store_item(conn, it["id"])
        stack.append(it)
        head = it["rebased"]
    said += _judge(conn, stack)
    if again and len(order(conn)) < len(stack):
        said += _walk(conn, again=False)           # one went unfit: those behind it move up now
    return said


def _result(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[str]:
    """'green', 'red', 'running' or None: gate 2 on this item's current rebased commit."""
    if not it["gate2_run"] or it["gate2_on"] != it["rebased"]:
        return None
    run = store.run(conn, it["gate2_run"])
    if run is None:
        return None
    if run["state"] in store.ACTIVE:
        return "running"
    return "green" if run["state"] == "done" else "red"


def _judge(conn: sqlite3.Connection, stack: List[sqlite3.Row], start: Optional[Callable] = None) -> List[str]:
    """Gate 2 for the stack as a batch: green on an item clears it and all under it;
    red on an item means one of the unjudged ones up to it is bad — check the middle.
    `start(conn, stack, lo, top)` starts the check (eki's own: _start_gate2)."""
    start = start or _start_gate2
    said: List[str] = []
    results = [_result(conn, it) for it in stack]
    green = max((i for i, r in enumerate(results) if r == "green"), default=-1)
    for it in stack[:green + 1]:
        if it["gate2"] != "green" and _set_if(conn, it, "queued", gate2="green"):
            said.append(f"item {it['id']}: gate 2 green"
                        + ("" if it["id"] == stack[green]["id"] else f" (in the batch checked on {stack[green]['id']})"))
    if "running" in results:
        return said                                # one check at a time: the rest wait for the next batch
    lo = green + 1
    if lo >= len(stack):
        return said
    red = next((i for i in range(lo, len(stack)) if results[i] == "red"), None)
    if red is None:
        return said + start(conn, stack, lo, len(stack) - 1)
    if red == lo:
        return said + _unfit(conn, stack[lo], store.run(conn, stack[lo]["gate2_run"]))
    return said + start(conn, stack, lo, (lo + red - 1) // 2)


def _rebase(conn: sqlite3.Connection, it: sqlite3.Row, head: str) -> tuple:
    """(the rebased sha or None, lines). None: it left the queue."""
    if it["gate2_run"]:
        _cancel(conn, it["gate2_run"])
    wt = it["worktree"]
    since = it["head"] if it["rebased"] else None
    try:
        if since and git(wt, "rev-parse", "HEAD") != it["rebased"]:
            since = None                           # moved by hand: rebase all of it
        conflicts = rebase.onto(wt, head, since=since)
    except (WorkspaceError, OSError) as e:
        if _set_if(conn, it, "queued", state="left", error=f"the rebase onto {head[:12]} failed: {e}"):
            return None, [f"item {it['id']}: left for you — the rebase failed: {e}"]
        return None, []
    if conflicts:
        rid = resolve.start(conn, it, head, conflicts)
        return None, [f"item {it['id']}: conflict in {', '.join(conflicts)} → resolving ({rid[:8]})"]
    sha = git(wt, "rev-parse", "HEAD")
    if not _set_if(conn, it, "queued", head=head, rebased=sha, gate2=None, gate2_run=None, gate2_on=None):
        return None, []
    return sha, [f"item {it['id']}: rebased on {head[:12]}"]


def _cancel(conn: sqlite3.Connection, rid: str) -> None:
    run = store.run(conn, rid)
    if run is not None and run["state"] in store.ACTIVE:
        store.cancel(conn, rid)


def _start_gate2(conn: sqlite3.Connection, stack: List[sqlite3.Row], lo: int, top: int) -> List[str]:
    """One gate-2 run on stack[top], judging stack[lo..top] together."""
    batch = stack[lo:top + 1]
    it = stack[top]
    tree = rebase.gate_tree(integration.repo(), f"gate2-{it['id']}", it["rebased"])
    goals = {b["goal_id"] for b in batch}
    owners = {r["owner"] for r in conn.execute(
        f"SELECT owner FROM goals WHERE id IN ({','.join('?' * len(goals))})", tuple(goals))}
    docs = all(doccheck.of_item(b) for b in batch)
    with db.tx(conn):
        row = conn.execute("SELECT state, rebased FROM items WHERE id=?", (it["id"],)).fetchone()
        if row["state"] != "queued" or row["rebased"] != it["rebased"]:
            return []
        title = it["title"] if len(batch) == 1 else f"batch of {len(batch)}, up to {it['title']}"
        tid = store.create_thread(conn, f"gate 2: {title}", str(tree), by="eki")
        judge = doccheck.command(batch[0]["head"], it["rebased"]) if docs else GATE2
        rid = store.create_run(conn, tid, judge, provider="command",
                               priority="now" if "you" in owners else "background")
        selfwork._set(conn, it["id"], gate2_run=rid, gate2_on=it["rebased"], gate2=None)
    what = f"item {it['id']}" if len(batch) == 1 else f"a batch of {len(batch)} ({batch[0]['id']}..{it['id']})"
    return [f"gate 2 on {what} at {it['rebased'][:12]} ({rid[:8]}){', docs only' if docs else ''}"]


def _unfit(conn: sqlite3.Connection, it: sqlite3.Row, run: sqlite3.Row) -> List[str]:
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-25:])
    tail = (tail + f"\n(gate 2 {run['state']}: {run['error'] or ''})").strip()
    if not _set_if(conn, it, "queued", state="unfit", gate2="red", verdict=tail[-800:], error="gate 2 failed"):
        return []
    return [f"item {it['id']}: unfit — gate 2 failed"]


# ---- landing ---------------------------------------------------------------------------------

def _land(conn: sqlite3.Connection) -> List[str]:
    if not order(conn) and not _ahead():
        return []
    said: List[str] = []
    landed = 0
    for it in order(conn):
        main = integration.main()
        if it["head"] != main or it["gate2"] != "green" or not it["rebased"]:
            break
        try:
            integration.fast_forward(it["rebased"])
        except integration.IntegrationError as e:
            log.warning("queue: item %s would not land: %s", it["id"], e)
            break
        if _set_if(conn, it, "queued", state="landed", landed_at=db.now()):
            landed += 1
            said.append(f"item {it['id']}: landed ({it['rebased'][:12]})")
        workspace.remove(integration.repo(), f"gate2-{it['id']}")
    if landed or _ahead():
        if not integration.push() and landed:
            said.append("queue: the push to origin failed; tried again next tick")
    if landed:
        integration.update_source()
    return said
