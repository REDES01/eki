"""Check slots: how many judge runs may run at once.

A judge run is a command run that checks a change — gate 1's bin/check,
gate 2's check in company, the docs check, the train's check before a swap,
a project's own check (eki/projectwork.py).
With the suite on every core, one check saturates the Mac: two fast checks
beat five crawling ones. So at most `machine.checks` (routing.json,
eki/machinecaps.py; default max(2, cores/6)) slots are in use at a time; the
rest stay queued. This is the engine's scheduling — the queue and self-work
just queue their runs as before.
Agents' own test runs inside build turns take the same slots through
`eki slot --` (eki/testslots.py), so busy counts their holds too.

A wide judge run (gate 2 and the train's check: `EKI_CHECK_WIDE=1` in its
command, so bin/check runs cores − 2 workers instead of a third) weighs
ceil(wide/narrow) slots, capped at the limit. Gate 2 is the one serial step
on the way to landing, so a waiting wide run goes first: the oldest ready one
is reserved, and no new narrow judge or `eki slot` hold starts until it has.
"""
from __future__ import annotations

import math
import os
import sqlite3
from typing import Optional

from . import db, machinecaps

#: what a judge run's command line carries
MARKS = ("bin/check", "eki.doccheck", "eki.candidate", "eki-judge")    # eki-judge: a project's check
#: what a wide judge run's command line carries (eki/queue.py GATE2, eki/traincheck.py)
WIDE = "EKI_CHECK_WIDE=1"


def is_judge(run: sqlite3.Row) -> bool:
    return run["provider"] == "command" and any(m in (run["prompt"] or "") for m in MARKS)


def is_wide(run: sqlite3.Row) -> bool:
    return is_judge(run) and WIDE in (run["prompt"] or "")


def limit() -> int:
    return machinecaps.settings()["checks"]


def weight(run: sqlite3.Row) -> int:
    """Slots a run takes: 1, or for a wide run its workers over a narrow run's."""
    if not is_wide(run):
        return 1
    cores = os.cpu_count() or 6
    wide, narrow = max(2, cores - 2), max(2, cores // 3)
    return min(limit(), math.ceil(wide / narrow))


def judging(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT provider, prompt FROM runs "
                        "WHERE state IN ('starting', 'running') AND provider='command'").fetchall()
    return sum(weight(r) for r in rows if is_judge(r))


def busy(conn: sqlite3.Connection) -> int:
    """Slots in use: running judge runs by weight plus agents' test runs holding one."""
    from . import testslots
    return judging(conn) + testslots.held()


def first_wide(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """The oldest ready queued wide judge run, in the engine's queue order."""
    rows = conn.execute("SELECT * FROM runs WHERE state='queued' AND cancel=0 "
                        "AND provider='command' AND (retry_at IS NULL OR retry_at <= ?) "
                        "AND instr(prompt, ?) > 0 ORDER BY "
                        "CASE priority WHEN 'now' THEN 0 ELSE 1 END, created_at",
                        (db.now(), WIDE)).fetchall()
    return next((r for r in rows if is_wide(r)), None)


def reserved(conn: sqlite3.Connection) -> int:
    """Slots kept free for the waiting wide run, else 0."""
    r = first_wide(conn)
    return weight(r) if r is not None else 0


def _fits(run: sqlite3.Row, used: int, held_for: int, cap: int) -> bool:
    if is_wide(run):
        return used + weight(run) <= cap
    return used + 1 + held_for <= cap


def waiting(conn: sqlite3.Connection, run: sqlite3.Row) -> bool:
    """A queued judge run held back only for want of a check slot."""
    if run["state"] != "queued" or not is_judge(run):
        return False
    first = first_wide(conn)
    held_for = weight(first) if first is not None and first["id"] != run["id"] else 0
    return not _fits(run, busy(conn), held_for, limit())


class Tick:
    """One engine.spawn_ready pass: slots in use, and those kept for the first wide run."""

    def __init__(self, conn: sqlite3.Connection, active: list) -> None:
        from . import testslots
        self.cap = limit()
        self.used = sum(weight(r) for r in active if is_judge(r)) + testslots.held()
        first = first_wide(conn)
        self.first = first["id"] if first is not None else None
        self.held_for = weight(first) if first is not None else 0

    def fits(self, run: sqlite3.Row) -> bool:
        return _fits(run, self.used, self.held_for, self.cap)

    def took(self, run: sqlite3.Row) -> None:
        self.used += weight(run)
        if run["id"] == self.first:
            self.held_for = 0
