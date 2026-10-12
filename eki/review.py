"""The review: an independent judge beside gate 1 (docs/self-build.md, "Judging").

When a build ends, `ask` starts a judge run (eki/judge.py) in the same
transaction as gate 1: the judge role's pick, in a run and thread of its own, in a
read-only worktree, it files one verdict per check — `spec` and the goal checks the
item carries — with `eki verdict` (eki/verdicts.py). items.review_run holds
the judge run. The item stays `judging` until both have ended (`ended`);
then `read` maps the verdicts: all passed → `ok`; any failed → `no` with
each failed check's suggestion; uncertain → `uncertain` (selfjudge starts one
second judge, then asks a person). With gate 1 green, `ok` proposes the item;
a first `no` sends it back to be built once more with the objection in its
brief (`objection`); a second `no` proposes it with the objection kept, and
the queue never takes it by itself. A judge that can't judge — no other
backend, the setting off, a failed run or a missing verdict — is
`none: <why>`: the judge never holds an item it couldn't judge.

A playtester card's gate 1 is itself a `play` judge run (eki/judge.py play),
registered here, and its verdicts are read beside the judge's.

Items left in `reviewing` by an older eki (the review used to start after
gate 1), and review chores an older eki started, are still read by their
`REVIEW:` line (`verdict`). Nothing is held in memory: each reads the item,
its runs and the judgings, and moves the item once, under `db.tx`, with a
state re-check.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Optional, Tuple

from . import chores, db, judge, selfwork, store, verdicts, workflow, workflowgates


def _settle(conn: sqlite3.Connection, it: sqlite3.Row, why: str) -> List[str]:
    selfwork._set(conn, it["id"], state="proposed", review=f"none: {why}")
    return [f"item {it['id']}: fit — proposed on {it['branch']} (not reviewed: {why})"]


def ask(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Called under db.tx with the item's new commit and gate 1 written: start its judge run
    (and make a playtester's gate 1 a `play` judge run). (judge run id, '') or (None, why).
    It never moves the item."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    card = workflowgates.of_goal(conn, goal) if goal is not None and goal["project"] else None
    if card is not None:
        judge.play(conn, it, card)
    if card is not None and not workflow.has(card, "review"):     # the project's card leaves it out
        return None, workflowgates.NO_REVIEW
    if card is None and not selfwork.settings().get("review", True):
        return None, "self.review is off"
    return judge.start_item(conn, it)


def ended(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """No review run, or it is no longer going."""
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    return run is None or run["state"] not in store.ACTIVE


def read(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Called under db.tx once the judge run ended: ('ok' | 'no' | 'uncertain' | None, why).
    A review chore an older eki started is read by its REVIEW: line, and its chore closed."""
    if (it["review_run"] and verdicts.judging(conn, it["review_run"]) is not None) or judge.played(conn, it):
        return judge.read(conn, it)
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    said, why = (None, "")
    if run is None:
        why = "the review run is gone"
    elif run["state"] != "done":
        why = f"the review run ended {run['state']}: {run['error'] or ''}".strip(" :")
    else:
        said, why = verdict(store.answer(conn, run["id"]))
        if said is None:
            why = "the review has no REVIEW: line"
    chore = chores.latest(conn, "review", it["id"])
    if chore is not None and it["review_run"] and chore["run_id"] == it["review_run"]:
        chores.close(conn, chore["id"], "done" if said else "failed", f"{said}: {why}" if said else why)
    return said, why


def verdict(answer: str) -> Tuple[Optional[str], str]:
    """('ok' | 'no' | None, why) from the last `REVIEW:` line of an answer."""
    m = None
    for m in re.finditer(r"^\s*[`*]*REVIEW:[`*]*\s*(ok|no)\b[\s:,.\-—]*(.*)$", answer or "", re.M | re.I):
        pass
    if m is None:
        return None, ""
    return m.group(1).lower(), m.group(2).strip(" `*") or "no reason given"


def conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Move a legacy `reviewing` item on once its review run has ended."""
    if not ended(conn, it):
        return []
    with db.tx(conn):
        cur = conn.execute("SELECT state, reviews FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != "reviewing":
            return []
        said, why = read(conn, it)
        if said is None:
            return _settle(conn, it, why)
        if said == "ok":
            selfwork._set(conn, it["id"], state="proposed", review="ok")
            return [f"item {it['id']}: reviewed ok — proposed on {it['branch']}"]
        if not cur["reviews"]:
            selfwork._set(conn, it["id"], state="waiting", review=f"no: {why}", reviews=1, verdict=None,
                          error="review: no — rebuilding once")
            return [f"item {it['id']}: the review says no — {why[:120]}; rebuilding once"]
        selfwork._set(conn, it["id"], state="proposed", review=f"no: {why}")
    return [f"item {it['id']}: the review says no again — proposed; `eki self apply {it['id']}` if you agree"]


_FAILED = re.compile(r"^(spec|c\d+):\s*(.+)$")


def objection(it: sqlite3.Row) -> Optional[str]:
    """The objection a rebuild must answer, if there is one: a judge's failed checks, a line each
    ("A judge found check c2 failed: <suggestion>"), or an older review's sentence."""
    got = it["review"] or ""
    if not got.startswith("no:"):
        return None
    said = got[len("no:"):].strip()
    lines = [ln.strip() for ln in said.splitlines() if ln.strip()]
    if lines and all(_FAILED.match(ln) for ln in lines):
        return "\n".join("A judge found check {} failed: {}".format(*_FAILED.match(ln).groups()) for ln in lines)
    return said or None
