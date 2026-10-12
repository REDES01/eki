"""Gate 1 and the review have ended for a `judging` eki item: move it on
(docs/self-build.md). Green goes by the review's word; red gets one more
build, then one fixer run (eki/fixer.py), then it is unfit — left for you
with what the fixer tried."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import db, fixer, review, selfwork, store


def conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Move a `judging` item once gate 1 and its review (if any) have both ended."""
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE or not review.ended(conn, it):
        return []
    tail = selfwork.failure_tail(store.answer(conn, run["id"]))
    if run["state"] != "done":
        tail = (tail + f"\n(checks {run['state']}: {selfwork.failure_tail(run['error'] or '', 6)})").strip()
    with db.tx(conn):
        cur = conn.execute("SELECT state, reviews FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != "judging":
            return []
        seen = it["review"]
        if it["review_run"]:
            said, why = review.read(conn, it)                # closes the chore (eki/review.py)
            seen = f"{said}: {why}" if said == "no" else said or f"none: {why}"
        if run["state"] == "done":
            return judged(conn, it, tail, seen, cur["reviews"])
        if it["tries"] - (cur["reviews"] or 0) < 2 and run["state"] == "failed":   # a review's rebuild is free
            selfwork._set(conn, it["id"], state="waiting", verdict=tail[-selfwork.FAILURE_CHARS:], review=seen,
                 error="checks failed; trying once more")
            return [f"item {it['id']}: checks failed; one more try"]
        selfwork._set(conn, it["id"], verdict=tail[-selfwork.FAILURE_CHARS:], review=seen)
        line = fixer.offer(conn, it, "gate1", tail)
        if line:
            return [line]
        selfwork._set(conn, it["id"], state="unfit", error=selfwork.with_tried(it, f"checks {run['state']}"))
    return [f"item {it['id']}: unfit — left for you"]


def judged(conn: sqlite3.Connection, it: sqlite3.Row, tail: str, seen: Optional[str],
           reviews: Optional[int]) -> List[str]:
    """Gate 1 green: the review's word decides (under the caller's tx)."""
    if not (seen or "").startswith("no:"):
        selfwork._set(conn, it["id"], state="proposed", verdict=tail[-800:], review=seen)
        how = "reviewed ok" if seen == "ok" else f"fit (not reviewed: {(seen or 'none: ?')[6:]})"
        return [f"item {it['id']}: {how} — proposed on {it['branch']}"]
    why = seen[3:].strip()
    if not reviews:
        selfwork._set(conn, it["id"], state="waiting", verdict=None, review=seen, reviews=1,
             error="review: no — rebuilding once")
        return [f"item {it['id']}: the review says no — {why[:120]}; rebuilding once"]
    selfwork._set(conn, it["id"], state="proposed", verdict=tail[-800:], review=seen)
    return [f"item {it['id']}: the review says no again — proposed; `eki self apply {it['id']}` if you agree"]
