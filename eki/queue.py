"""The queue: speculative rebase, gate 2 and landing (docs/self-build.md,
"The queue" and "Judging").

Queued items stand in order (queued_at, then created_at). Each one's
predicted head is integration main plus the rebased commits of the items
ahead of it, so every rebase and every gate-2 run starts at once, not when
the ones ahead have landed. A conflict sends the item to the side path
(resolve.py) and the items behind it are rebased without it; a red gate 2
makes it unfit and the items behind it are judged again on new heads. The
front of the queue lands — a fast-forward of integration main — once its
gate 2 is green on the head main is at.

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
from typing import Any, List

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
    """self.autonomy in routing.json: 'apply' queues fit items by itself, 'propose' waits for you."""
    if mode not in MODES:
        raise ValueError(f"autonomy is one of {', '.join(MODES)}, not {mode!r}")
    path = paths.config("routing")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data["self"] = {**(data.get("self") or {}), "autonomy": mode}
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def order(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """The queued items, front first."""
    return conn.execute("SELECT * FROM items WHERE state='queued' "
                        "ORDER BY COALESCE(queued_at, created_at), created_at").fetchall()


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


def _set_if(conn: sqlite3.Connection, it: sqlite3.Row, was: str, **fields: Any) -> bool:
    """Write fields when the item is still in state `was` (a restart or a person may have moved it)."""
    with db.tx(conn):
        row = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
        if row is None or row["state"] != was:
            return False
        selfwork._set(conn, it["id"], **fields)
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
            if _set_if(conn, it, "proposed", state="locked", locked=db.dumps(held)):
                said.append(f"item {it['id']}: locked — touches {', '.join(held)}; needs a person")
        elif apply_mode:
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


def _walk(conn: sqlite3.Connection) -> List[str]:
    items = order(conn)
    if not items:
        return []
    if _needs_rebase(items, integration.main()):
        integration.sync()                         # eki fetches before every rebase
    head = integration.main()
    said: List[str] = []
    for it in order(conn):
        if it["head"] != head or not it["rebased"]:
            moved = _rebase(conn, it, head)
            said += moved[1]
            if moved[0] is None:
                continue                           # out of the queue: head doesn't advance past it
            it = selfwork.store_item(conn, it["id"])
        run = store.run(conn, it["gate2_run"]) if it["gate2_run"] else None
        if it["gate2_on"] != it["rebased"] or run is None:
            said += _start_gate2(conn, it)
        elif run["state"] == "done":
            if it["gate2"] != "green" and _set_if(conn, it, "queued", gate2="green"):
                said.append(f"item {it['id']}: gate 2 green")
        elif run["state"] not in store.ACTIVE:
            said += _unfit(conn, it, run)
            continue
        head = it["rebased"]
    return said


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


def _start_gate2(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    tree = rebase.gate_tree(integration.repo(), f"gate2-{it['id']}", it["rebased"])
    goal = conn.execute("SELECT owner FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    with db.tx(conn):
        row = conn.execute("SELECT state, rebased FROM items WHERE id=?", (it["id"],)).fetchone()
        if row["state"] != "queued" or row["rebased"] != it["rebased"]:
            return []
        tid = store.create_thread(conn, f"gate 2: {it['title']}", str(tree))
        docs = doccheck.of_item(it)
        judge = doccheck.command(it["head"], it["rebased"]) if docs else GATE2
        rid = store.create_run(conn, tid, judge, provider="command",
                               priority="now" if goal and goal["owner"] == "you" else "background")
        selfwork._set(conn, it["id"], gate2_run=rid, gate2_on=it["rebased"], gate2=None)
    return [f"item {it['id']}: gate 2 on {it['rebased'][:12]} ({rid[:8]}){', docs only' if docs else ''}"]


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
