"""Gate 1 and the judge have ended for a `judging` eki item: move it on
(docs/self-build.md). Green goes by the judge's word (eki/review.py,
eki/judge.py); red gets one more build, then one fixer run (eki/fixer.py),
then it is unfit — left for you with what the fixer tried. A judge that is
uncertain gets one second judge on another backend (judge.second); still
uncertain, the item is proposed for you and the queue never takes it."""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import db, fixer, itemhold, judge, review, selfwork, store


def conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """Move a `judging` item once gate 1 and its review (if any) have both ended."""
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE or not review.ended(conn, it):
        return []
    tail = selfwork.failure_tail(store.answer(conn, run["id"]))
    if run["state"] != "done":
        tail = (tail + f"\n(checks {run['state']}: {selfwork.failure_tail(run['error'] or '', 6)})").strip()
    with db.tx(conn):
        cur = conn.execute("SELECT state, reviews, review_run FROM items WHERE id=?", (it["id"],)).fetchone()
        if cur is None or cur["state"] != "judging" or cur["review_run"] != it["review_run"]:
            return []                                        # moved, or a second judge started: next tick
        seen = it["review"]
        if it["review_run"] or judge.played(conn, it):
            said, why = review.read(conn, it)                # the judge's verdicts (eki/judge.py)
            if said == "uncertain" and run["state"] == "done":
                return unsure(conn, it, tail, why)
            seen = f"{said}: {why}" if said in ("no", "uncertain") else said or f"none: {why}"
        if run["state"] == "done":
            return judged(conn, it, tail, seen, cur["reviews"])
        if (h := itemhold.settle(conn, it, run)):           # a broken setup: held, no try spent
            selfwork._set(conn, it["id"], review=seen)
            return h
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


def unsure(conn: sqlite3.Connection, it: sqlite3.Row, tail: str, why: str) -> List[str]:
    """Gate 1 green, a check uncertain (under the caller's tx): one second judge on another
    backend; after it, or with none to ask, the item is proposed for you."""
    if not judge.is_second(conn, it):
        rid = judge.second(conn, it)
        if rid:
            return [f"item {it['id']}: a judge is uncertain — {why[:120]}; a second judge looks ({rid[:8]})"]
    seen = f"uncertain: {why}"
    selfwork._set(conn, it["id"], state="proposed", verdict=tail[-800:], review=seen)
    selfwork.needs_you(conn, it, "proposed", f"no judge could settle {why}", f"eki self show {it['id']}", "judgment")
    return [f"item {it['id']}: a judge is still uncertain — {why[:120]}; proposed, "
            f"`eki self apply {it['id']}` if you agree"]
