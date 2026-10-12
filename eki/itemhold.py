"""An item whose run stopped on a broken setup waits instead of failing
(docs/self-build.md, Items).

When a run of an item ended not done and its `runs.stop` (eki/stopkind.py) says
`environment`, a retry would fail the same way until a person fixes the setup:
the item goes back to `waiting` with `items.hold` and rechecks every
`stopkind.ENV_RECHECK` seconds. That costs no attempt, and the person is pinged
once per distinct fix, however many rechecks fail. Every other stop is the
caller's own path: one more try, the fixer, unfit or left.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import List, Optional

from . import db, notify, store, stopkind


def stop_of(run: Optional[sqlite3.Row]) -> Optional[dict]:
    """The run's `runs.stop` record, if it has one."""
    if run is None or "stop" not in run.keys() or not run["stop"]:
        return None
    try:
        got = json.loads(run["stop"])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def hold_of(it: sqlite3.Row) -> Optional[dict]:
    """The item's `items.hold`, if it is held."""
    if "hold" not in it.keys() or not it["hold"]:
        return None
    try:
        got = json.loads(it["hold"])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def waits(it: sqlite3.Row, now: float) -> bool:
    """A waiting item held until a recheck still to come."""
    h = hold_of(it)
    return it["state"] == "waiting" and h is not None and (h.get("retry_at") or 0) > now


def environment(run: Optional[sqlite3.Row], now: float) -> Optional[dict]:
    """The hold for a run that ended not done on an environment stop; else None."""
    stop = stop_of(run)
    if run is None or run["state"] == "done" or stop is None or stop.get("kind") != "environment":
        return None
    return {**stop, "fix": stop.get("fix") or "the setup is broken", "retry_at": now + stopkind.ENV_RECHECK,
            "run": run["id"]}


def key(iid: str, fix: str) -> str:
    """The needs-you key: one ping per item and distinct fix."""
    return f"item:{iid}:env:{hashlib.sha1(fix.encode()).hexdigest()[:10]}"


def error(hold: dict) -> str:
    return f"{hold['fix']} (held; rechecks in {stopkind.ENV_RECHECK // 60} min)"


def settle(conn: sqlite3.Connection, it: sqlite3.Row, run: Optional[sqlite3.Row]) -> Optional[List[str]]:
    """The item's run ended not done. On an environment stop hold the item and say so
    (inside the caller's transaction); otherwise None and the caller goes on as before."""
    now = db.now()
    hold = environment(run, now)
    if hold is None:
        return None
    fix = hold["fix"]
    conn.execute("UPDATE items SET state='waiting', hold=?, error=?, updated_at=? WHERE id=?",
                 (db.dumps(hold), error(hold), now, it["id"]))
    body = " ".join(f"{it['title']} — setup broken: {fix}: eki self show {it['id']}".split())[:200]
    notify.queue(conn, key(it["id"], fix), "needs_you", "eki · needs you", body)
    return [f"item {it['id']}: held — {stopkind.describe(hold, now)}"]


def line(c: Optional[sqlite3.Connection], it: sqlite3.Row, *, queued_only: bool = False) -> Optional[str]:
    """Why the item is stopped, in one line: its hold, else its current run's stop.
    `queued_only`: the run's stop counts only while the run waits queued to go again."""
    now = db.now()
    h = hold_of(it)
    if h is not None:
        return stopkind.describe(h, now)
    if c is None or not it["run_id"]:
        return None
    run = store.run(c, it["run_id"])
    stop = stop_of(run)
    if stop is None or (queued_only and run["state"] != "queued"):
        return None
    return stopkind.describe(stop, now)
