"""The review: a second reader beside gate 1 (docs/self-build.md, "Judging").

When a build ends, `ask` starts a `review` chore in the same transaction as
gate 1: the reviewer (routing.json `roles.reviewer`, by default the local
model; eki/chores.py) reads whether the diff does what the spec and the
builder's summary say while the checks run. The item stays `judging` until
both have ended (`ended`); then selfwork reads the review's last `REVIEW:`
line (`read`). With gate 1 green, `ok` proposes the item; a first `no` sends
it back to be built once more with the objection in its brief; a second `no`
proposes it with the objection kept, and the queue never takes it by itself.
A review that can't judge — no reviewer that can run, the setting off, a
failed run or an answer without the line — is `none: <why>`: the reviewer
never holds an item it couldn't judge.

Items left in `reviewing` by an older eki (the review used to start after
gate 1) are still moved on by `conclude`. Nothing is held in memory: each
reads the item, its runs and the chore, and moves the item once, under
`db.tx`, with a state re-check.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import chores, db, integration, projects, roles, selfwork, store, workflow, workflowgates, workspace
from .components import knowledge

GOAL_MAX = 2000
DIFF_MAX = 40000

PROMPT = """\
You are a second reader of a change to eki, a program on this Mac. Another
agent built the change; its checks are running beside you. Read it and judge it — do not
change anything.

The goal the change is part of:

{goal}

The change: **{title}**

{spec}

What its builder said it did:

{summary}
{brief}
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


def ask(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Called under db.tx with the item's new commit written: start its review
    chore. (review run id, '') or (None, why). It never moves the item."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    card = workflowgates.of_goal(conn, goal) if goal is not None and goal["project"] else None
    if card is not None and not workflow.has(card, "review"):     # the project's card leaves it out
        return None, workflowgates.NO_REVIEW
    if card is None and not selfwork.settings().get("review", True):
        return None, "self.review is off"
    priority = "now" if goal is None or goal["owner"] == "you" else "background"
    pick = roles.pick("reviewer", conn, background=priority == "background")
    if pick.provider is None:
        return None, "the reviewer role is off" if pick.target == "off" else pick.why
    rid = chores.start(conn, "review", it["id"], prompt(conn, it), priority, role="reviewer")
    return (rid, "") if rid is not None else (None, "no reviewer")


def ended(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """No review run, or it is no longer going."""
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    return run is None or run["state"] not in store.ACTIVE


def read(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Called under db.tx once the review run ended: ('ok' | 'no' | None, why),
    and its chore closed."""
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


def prompt(conn: sqlite3.Connection, it: sqlite3.Row) -> str:
    goal = conn.execute("SELECT text, project FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    base, sha = it["base"] or "", it["commit_sha"] or ""
    where = it["worktree"] if it["worktree"] and Path(it["worktree"]).exists() else integration.repo()
    diff = workspace.git(where, "diff", f"{base}..{sha}", check=False) if base and sha else ""
    cut = ""
    if len(diff) > DIFF_MAX:
        cut = (f"\n(The diff is {len(diff)} characters; only the first {DIFF_MAX} are shown —"
               f" {len(diff) - DIFF_MAX} were cut.)\n")
        diff = diff[:DIFF_MAX]
    told = _told(conn, it, goal)
    return PROMPT.format(goal=((goal["text"] if goal else "") or "(no goal text)").strip()[:GOAL_MAX],
                         title=it["title"], spec=(it["spec"] or "").strip() or "(no spec)",
                         summary=(it["summary"] or "").strip() or "(nothing said)",
                         brief=f"\nWhat the builder was told to read:\n\n{told}\n" if told else "",
                         base=base[:12], sha=sha[:12], diff=diff.strip() or "(empty)", cut=cut)


def _told(conn: sqlite3.Connection, it: sqlite3.Row, goal: Optional[sqlite3.Row]) -> str:
    """The knowledge brief the item's builder got ('' for eki's own items, which have no cards)."""
    try:
        where = it["worktree"] if it["worktree"] and Path(it["worktree"]).exists() else None
        if where is None and goal is not None and goal["project"]:
            p = projects.get(conn, goal["project"])
            where = p["path"] if p is not None else None
        return knowledge.brief(where, conn, context=knowledge.names(it["context"])) if where else ""
    except Exception:  # noqa: BLE001 — the brief never keeps an item from its review
        return ""


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


def objection(it: sqlite3.Row) -> Optional[str]:
    """The review's objection a rebuild must answer, if there is one."""
    got = it["review"] or ""
    return got[len("no:"):].strip() or None if got.startswith("no:") else None
