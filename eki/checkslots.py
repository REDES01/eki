"""Check slots: how many judge runs may run at once.

A judge run is a command run that checks a change — gate 1's bin/check,
gate 2's check in company, the docs check, the train's check before a swap.
With the suite on every core, one check saturates the Mac: two fast checks
beat five crawling ones. So at most `self.check_slots` (routing.json,
default 2) run at a time; the rest stay queued. This is the engine's
scheduling — the queue and self-work just queue their runs as before.
"""
from __future__ import annotations

import sqlite3

from . import selfwork

#: what a judge run's command line carries
MARKS = ("bin/check", "eki.doccheck", "eki.candidate")


def is_judge(run: sqlite3.Row) -> bool:
    return run["provider"] == "command" and any(m in (run["prompt"] or "") for m in MARKS)


def limit() -> int:
    try:
        n = int(selfwork.settings().get("check_slots", 2))
    except (TypeError, ValueError):
        n = 2
    return max(1, n)


def busy(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT provider, prompt FROM runs "
                        "WHERE state IN ('starting', 'running') AND provider='command'").fetchall()
    return sum(1 for r in rows if is_judge(r))


def waiting(conn: sqlite3.Connection, run: sqlite3.Row) -> bool:
    """A queued judge run held back only for want of a check slot."""
    return run["state"] == "queued" and is_judge(run) and busy(conn) >= limit()
