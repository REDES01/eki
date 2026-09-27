"""The review: a second reader between gate 1 and the queue (docs/self-build.md,
"Judging").

When gate 1 is green the item goes to `reviewing` and a `review` chore asks
the local model (eki/chores.py) whether the diff does what the spec and the
builder's summary say. Its last `REVIEW:` line decides: `ok` proposes the
item; a first `no` sends it back to be built once more with the objection in
its brief; a second `no` proposes it with the objection kept, and the queue
never takes it by itself. A review that can't judge — no local model, the
setting off, a failed run or an answer without the line — proposes the item
with `none: <why>`: the reviewer never holds an item it couldn't judge.

Nothing is held in memory: `conclude` reads the item, its review run and the
chore, and moves the item once, under `db.tx`, with a state re-check.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import chores, db, integration, selfwork, store, workspace

GOAL_MAX = 2000
DIFF_MAX = 40000

PROMPT = """\
You are a second reader of a change to eki, a program on this Mac. Another
agent built the change; its checks passed. Read it and judge it — do not
change anything.

The goal the change is part of:

{goal}

The change: **{title}**

{spec}

What its builder said it did:

{summary}

The diff (git diff {base}..{sha}):

```diff
{diff}
```
{cut}
Does the diff do what the spec and the summary say, without obvious bugs,
missing tests or stray changes? Say briefly what you checked and what you
found. End your answer with exactly one line, nothing after it:

REVIEW: ok
or
REVIEW: no <why, in one sentence>
"""


def _settle(conn: sqlite3.Connection, it: sqlite3.Row, why: str) -> List[str]:
    selfwork._set(conn, it["id"], state="proposed", review=f"none: {why}")
    return [f"item {it['id']}: fit — proposed on {it['branch']} (not reviewed: {why})"]


def start(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Called under db.tx when gate 1 is green: review the item, or propose it as it is."""
    if not selfwork.settings().get("review", True):
        return _settle(conn, it, "self.review is off")
    if chores.local() is None:
        return _settle(conn, it, "no local model")
    goal = conn.execute("SELECT text, owner FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    priority = "now" if goal is None or goal["owner"] == "you" else "background"
    rid = chores.start(conn, "review", it["id"], prompt(conn, it), priority)
    if rid is None:
        return _settle(conn, it, "no local model")
    selfwork._set(conn, it["id"], state="reviewing", review_run=rid, review=None)
    return [f"item {it['id']}: fit — reviewing ({rid[:8]})"]


def prompt(conn: sqlite3.Connection, it: sqlite3.Row) -> str:
    goal = conn.execute("SELECT text FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    base, sha = it["base"] or "", it["commit_sha"] or ""
    where = it["worktree"] if it["worktree"] and Path(it["worktree"]).exists() else integration.repo()
    diff = workspace.git(where, "diff", f"{base}..{sha}", check=False) if base and sha else ""
    cut = ""
    if len(diff) > DIFF_MAX:
        cut = (f"\n(The diff is {len(diff)} characters; only the first {DIFF_MAX} are shown —"
               f" {len(diff) - DIFF_MAX} were cut.)\n")
        diff = diff[:DIFF_MAX]
    return PROMPT.format(goal=((goal["text"] if goal else "") or "(no goal text)").strip()[:GOAL_MAX],
                         title=it["title"], spec=(it["spec"] or "").strip() or "(no spec)",
                         summary=(it["summary"] or "").strip() or "(nothing said)",
                         base=base[:12], sha=sha[:12], diff=diff.strip() or "(empty)", cut=cut)


def verdict(answer: str) -> Tuple[Optional[str], str]:
    """('ok' | 'no' | None, why) from the last `REVIEW:` line of an answer."""
    m = None
    for m in re.finditer(r"^\s*[`*]*REVIEW:[`*]*\s*(ok|no)\b[\s:,.\-—]*(.*)$", answer or "", re.M | re.I):
        pass
    if m is None:
        return None, ""
    return m.group(1).lower(), m.group(2).strip(" `*") or "no reason given"


def conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Move a `reviewing` item on once its review run has ended."""
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    if run is not None and run["state"] in store.ACTIVE:
        return []
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
    with db.tx(conn):
        cur = conn.execute("SELECT state, reviews FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != "reviewing":
            return []
        if chore is not None and chore["run_id"] == it["review_run"]:
            chores.close(conn, chore["id"], "done" if said else "failed", f"{said}: {why}" if said else why)
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


def objection(it: sqlite3.Row) -> Optional[str]:
    """The review's objection a rebuild must answer, if there is one."""
    got = it["review"] or ""
    return got[len("no:"):].strip() or None if got.startswith("no:") else None
