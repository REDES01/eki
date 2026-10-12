"""The queue: speculative rebase, gate 2 and landing (docs/self-build.md,
"The queue" and "Judging").

Queued items stand in order (queued_at, then created_at). Each one's
predicted head is integration main plus the rebased commits of the items
ahead of it — a stack. A conflict sends the item to the side path
(resolve.py) and the items behind it are rebased without it. A resolved item
comes back at the back for gate 2, with `resolved_on` the head it was
resolved onto; each re-stack moves only the commits above that head
(`rebase --onto`), so an item under it that left the queue is left behind —
the line names it — and only a conflict resolves it again.

Gate 2 checks the stack as one batch: one run on the tip, which carries every
item under it. Green lands the whole batch. Red bisects: the middle of the
unjudged part is checked next, so one bad item is found in log2(n) runs and
the good ones before it land; then the bad one, at the front, gets its one
fixer run on the main they made (eki/fixer.py; it leaves the queue, `fixing`)
or is unfit, and the items behind it are rebased without it and checked
again. One check at a time: items that join while it runs wait for the next
batch, they don't restart it. The front
lands — a fast-forward of integration main — once gate 2 is green on it or
on an item above it in the stack.

Gate 2 is the fast tests only (bin/check with EKI_CHECK_FAST=1: no drills);
the drills and the candidate checks run on the train, before the swap. A
docs-only item (doccheck.py) is judged by the doccheck instead: its files
parse as UTF-8 text and nothing outside the docs lane changed.

The rules are eki/workstep_queue.py's, the same for a project's queue
(eki/projectqueue.py): this module is eki's executor. It looks (git, the
runs), feeds what it found (eki/workhandlers_queue.py `say`) and does what
step asks — the walk and gate 2 in eki/queuestack.py, the landing here.
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
from typing import Any, List, Optional, Tuple

# rebase: the plumbing tests stub (queue.rebase.onto)
from . import (db, doccheck, integration, locks, paths, projects, projectwork, queuestack, rebase, resolve,
               selfwork, workspace, workstep)
from .workhandlers_queue import event, say
from .workspace import WorkspaceError, git

log = logging.getLogger("eki.queue")

#: gate 2, run in a fresh detached worktree of the predicted head plus the item
GATE2 = '["/bin/sh", "-c", "EKI_PYTHON=$PWD/.venv/bin/python EKI_CHECK_WIDE=1 EKI_CHECK_FAST=1 exec bin/check -q"]'
MODES = ("apply", "propose")
#: states that give the queue something to do
BUSY = ("proposed", "locked", "queued", "resolving", "rechecking")


# ---- asked for by hand ----------------------------------------------------------------------

def apply(conn: sqlite3.Connection, iid: str, *, yes: bool = False) -> str:
    """Queue one item: a proposed one, or a locked one with the person's yes (a person_decided
    event; workstep.Refused, a ValueError, says why not)."""
    it = selfwork.store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    try:
        say(conn, it["goal_id"], it["id"], event("person_decided", decision="apply", yes=yes),
            then=lambda c, old, new: selfwork._set(c, it["id"], error=None))
    except workstep.Refused as e:
        if it["state"] == "locked" and not yes and it["locked"]:     # the files it was locked for
            files = ", ".join(json.loads(it["locked"])) or "?"
            raise workstep.Refused(f"item {it['id']} touches locked files: {files}, needs --yes") from e
        raise
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
    main = integration.main()
    said += land(conn)
    if order(conn) and integration.main() != main:
        said += _walk(conn)                        # a red one waiting for those under it is at the front now
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


def _own(conn: sqlite3.Connection, states: tuple) -> List[sqlite3.Row]:
    marks = ",".join("?" * len(states))
    return conn.execute(f"SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project IS NULL "
                        f"AND i.state IN ({marks}) ORDER BY i.created_at", states).fetchall()


def _in_main(sha: Optional[str], source_main: Optional[str] = None) -> bool:
    """Already in the target, eki's way: integration main, or the source's main (merged by hand)."""
    if not sha:
        return False
    if integration.contains(sha, "main"):
        return True
    return bool(source_main) and projectwork.is_ancestor(selfwork.source(), sha, source_main)


def _admit(conn: sqlite3.Connection) -> List[str]:
    """What git says of eki's proposed, locked and queued items: in main already, or can it be queued."""
    waiting = _own(conn, ("proposed", "locked", "queued"))
    if not waiting:
        return []
    repo = integration.repo()
    apply_mode = selfwork.settings().get("autonomy") == "apply"
    said = []
    for it in waiting:
        sha = (it["rebased"] or it["commit_sha"]) if it["state"] == "queued" else it["commit_sha"]
        there = _in_main(sha)
        queueable = (it["state"] == "proposed" and bool(it["worktree"]) and workspace.belongs(it["worktree"], repo))
        if not there and not queueable:
            continue                               # built before the queue: merged by hand
        facts: dict = dict(in_target=there, queueable=queueable, where=f"{(sha or '')[:12]} is in main", sha=sha)
        if queueable:                              # what step decides the admit from
            facts.update(locked=locks.locked_in(json.loads(it["touched"] or "[]") + json.loads(it["files"] or "[]")),
                         apply=apply_mode)
        said += say(conn, it["goal_id"], it["id"], event("seen", **facts), then=_admitted(it, facts), after=_seen)
    return said


def _admitted(it: sqlite3.Row, facts: dict):
    """The columns that go with what `seen` decides for `it`: a locked one keeps the files it was
    locked for — the very list step decided from."""
    def then(c: sqlite3.Connection, old: Any, new: Any) -> None:
        if new.stage == "locked":
            selfwork._set(c, it["id"], locked=db.dumps(facts.get("locked") or []))
        elif new.stage in ("landed", "applied") and old is not None and old.stage != new.stage:
            selfwork._set(c, it["id"], landed_at=db.now())
    return then


def _seen(conn: sqlite3.Connection, old: Any, new: Any) -> None:
    if old is not None and old.stage in ("queued", "gate 2") and new.stage in ("landed", "applied"):
        workspace.remove(integration.repo(), f"gate2-{new.id}")


def settle(conn: sqlite3.Connection) -> List[str]:
    """A proposed item whose commit is now in its target — integration main or the source's main
    (where you merged it by hand) for eki's, the project's branch for a project's — is applied;
    items that depend on it may start (from a base that has it)."""
    said: List[str] = []
    own, theirs = projectwork.split(conn, selfwork.items_in(conn, ("proposed",)))
    for it, project in theirs:
        if it["commit_sha"] and projectwork.merged(project, it["commit_sha"]):
            where = f"{it['commit_sha'][:12]} is in {project['name']} {project['branch']}"
            said += say(conn, it["goal_id"], it["id"], event("seen", in_target=True, where=where))
    if not own:
        return said
    main = workspace.head(selfwork.source())
    for it in own:
        if _in_main(it["commit_sha"], main):
            where = f"{it['commit_sha'][:12]} is in main ({main[:12]})"
            said += say(conn, it["goal_id"], it["id"], event("seen", in_target=True, where=where))
    return said


def _queue(conn: sqlite3.Connection) -> queuestack.Queue:
    return queuestack.Queue(rows=lambda: order(conn), tip=integration.main, sync=integration.sync)


def _walk(conn: sqlite3.Connection) -> List[str]:
    return queuestack.walk(conn, _queue(conn))


def _rebase(conn: sqlite3.Connection, it: sqlite3.Row, head: str) -> tuple:
    """(the rebased sha or None, lines) for one of eki's queued items. None: it left the queue."""
    return queuestack.rebase_one(conn, _queue(conn), it, head)


def gate2_command(conn: sqlite3.Connection, project: Any, batch: List[sqlite3.Row]) -> Tuple[Optional[str], bool]:
    """(gate 2's command for the batch, docs only?): eki's fast tests or the doccheck; a project's check."""
    if project.kind != "eki":
        row = projects.get(conn, project.id)
        return (projects.batch_argv(row) if row is not None else None), False
    if all(doccheck.of_item(b) for b in batch):
        return doccheck.command(batch[0]["head"], batch[-1]["rebased"]), True
    return GATE2, False


# ---- landing ---------------------------------------------------------------------------------

def land(conn: sqlite3.Connection, carry: bool = True) -> List[str]:
    """The front lands — a fast-forward of integration main — once gate 2 is green on it and it
    sits on main; then the next one. The land handler's plumbing (eki/workhandlers_queue.py)."""
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
        said += say(conn, it["goal_id"], it["id"], event("landed", went="target", sha=it["rebased"]),
                    then=lambda c, old, new, iid=it["id"]: selfwork._set(c, iid, landed_at=db.now()),
                    carry=carry)
        landed += selfwork.store_item(conn, it["id"])["state"] == "landed"
        workspace.remove(integration.repo(), f"gate2-{it['id']}")
    if landed or _ahead():
        if not integration.push() and landed:
            said.append("queue: the push to origin failed; tried again next tick")
    if landed:
        integration.update_source()
    return said
