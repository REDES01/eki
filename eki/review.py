"""The review: an independent judge beside gate 1 (docs/self-build.md, "Judging").

When a build ends, its run_gate 1 and start_run review actions
(eki/workstep.py, carried by eki/workhandlers_build.py) start gate 1 and,
through `ask`, a judge run (eki/judge.py): the judge role's pick, in a run and
thread of its own, in a read-only worktree, it files one verdict per check —
`spec` and the goal checks the item carries — with `eki verdict`
(eki/verdicts.py). items.review_run holds the judge run. The item stays
`judging` until both have ended (`ended`); then the executor reads the
verdicts (`said`) into a check_verdict event and the work model decides:
all passed → `ok`; any failed → `no` with each failed check's suggestion;
uncertain → `uncertain` (one second judge, `second`, then a person). With
gate 1 green, `ok` proposes the item; a first `no` sends it back to be built
once more with the objection in its brief (`objection`); a second `no`
proposes it with the objection kept, and the queue never takes it by itself.
A judge that can't judge — no other backend, the setting off, a failed run
or a missing verdict — is `none: <why>`: the judge never holds an item it
couldn't judge.

A playtester card's gate 1 is itself a `play` judge run (eki/judge.py play,
registered by the gate-1 handler), and its verdicts are read beside the judge's.

Items left in `reviewing` by an older eki (the review used to start after
gate 1), and review chores an older eki started, are still read by their
`REVIEW:` line (`verdict`) and moved on by `conclude`. Nothing is held in memory.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Optional, Tuple

from . import chores, judge, store, verdicts


def ask(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """Called under db.tx with the item's new commit written, when its gates hold a review:
    start its judge run. (judge run id, '') or (None, why). It never moves the item."""
    return judge.start_item(conn, it)


def ended(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """No review run, or it is no longer going."""
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    return run is None or run["state"] not in store.ACTIVE


def _judged(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """Is the item judged by a judge run (or a playtester's `play` gate 1) rather than an older review chore?"""
    return bool((it["review_run"] and verdicts.judging(conn, it["review_run"]) is not None) or judge.played(conn, it))


def said(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """The judge's word once its run ended — 'ok', 'no: <why>', 'uncertain: <why>' or 'none: <why>';
    the item's own `review` when it had no judge — and what an older review chore closes with.
    It writes nothing."""
    if _judged(conn, it):
        word, why = judge.read(conn, it)
        if word is None:
            return f"none: {why}", why
        return (f"{word}: {why}" if word in ("no", "uncertain") else word), f"{word}: {why}".rstrip(": ")
    if not it["review_run"]:
        return it["review"], ""
    run = store.run(conn, it["review_run"])
    if run is None:
        return "none: the review run is gone", "the review run is gone"
    if run["state"] != "done":
        why = f"the review run ended {run['state']}: {run['error'] or ''}".strip(" :")
        return f"none: {why}", why
    word, why = verdict(store.answer(conn, run["id"]))
    if word is None:
        return "none: the review has no REVIEW: line", "the review has no REVIEW: line"
    return (f"no: {why}" if word == "no" else word), f"{word}: {why}"


def close(conn: sqlite3.Connection, it: sqlite3.Row, seen: Optional[str], result: str) -> None:
    """Under db.tx, once the review's word is taken: its chore closed with it."""
    chore = chores.latest(conn, "review", it["id"])
    if chore is not None and it["review_run"] and chore["run_id"] == it["review_run"]:
        judged = seen == "ok" or (seen or "").startswith("no:")
        chores.close(conn, chore["id"], "done" if judged else "failed", result)


def verdict(answer: str) -> Tuple[Optional[str], str]:
    """('ok' | 'no' | None, why) from the last `REVIEW:` line of an answer."""
    m = None
    for m in re.finditer(r"^\s*[`*]*REVIEW:[`*]*\s*(ok|no)\b[\s:,.\-—]*(.*)$", answer or "", re.M | re.I):
        pass
    if m is None:
        return None, ""
    return m.group(1).lower(), m.group(2).strip(" `*") or "no reason given"


def conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """A legacy `reviewing` item whose review run has ended: its word, fed to the work model."""
    if not ended(conn, it):
        return []
    from . import buildfeed            # buildfeed reads reviews: import it late
    return buildfeed.verdict(conn, it, {"branch": it["branch"]})


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
