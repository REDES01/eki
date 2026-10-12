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

Checks go first. A hold polls about once a second while the engine starts
queued checks only on its tick, so holds used to win every freed slot (seen
2026-10-11: agents' holds kept all 3 slots and 18 checks sat queued for 25
minutes). Now a hold that hasn't started waits while `ahead` counts a queued
check that could start but for a slot; running holds finish. Among checks
the engine starts the train's first, then gate 2, then gate 1 (`first`). `ahead` counts a wide run by its
weight, so a hold needs no separate `reserved` on top of it.
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
    wide = [r for r in rows if is_wide(r)]
    wide.sort(key=lambda r: rank(conn, r))          # the train's before gate 2's, whichever came first
    return wide[0] if wide else None


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
    held_for = (weight(first) if first is not None and first["id"] != run["id"]
                and rank(conn, run) >= rank(conn, first) else 0)   # never held back for a run ranked after it
    return not _fits(run, busy(conn), held_for, limit())


class Tick:
    """One engine.spawn_ready pass: slots in use, and those kept for the first wide run."""

    def __init__(self, conn: sqlite3.Connection, active: list) -> None:
        from . import testslots
        self.cap = limit()
        self.used = sum(weight(r) for r in active if is_judge(r)) + testslots.held()
        first = first_wide(conn)
        self.conn = conn
        self.first = first["id"] if first is not None else None
        self.first_rank = rank(conn, first) if first is not None else 0
        self.held_for = weight(first) if first is not None else 0

    def fits(self, run: sqlite3.Row) -> bool:
        # the slots kept for the waiting wide run never hold back a run ranked before it (the train)
        held = self.held_for if run["id"] != self.first and rank(self.conn, run) >= self.first_rank else 0
        return _fits(run, self.used, held, self.cap)

    def took(self, run: sqlite3.Row) -> None:
        self.used += weight(run)
        if run["id"] == self.first:
            self.held_for = 0


def ahead(conn: sqlite3.Connection) -> int:
    """Slots wanted by queued judge runs that could start now but for a check slot (a wide
    run by its weight) — holds wait for them.
    A run whose thread is busy doesn't count (a build turn's hold would wait on its
    own thread's check); a background one counts only when the machine has room."""
    rows = conn.execute(
        "SELECT r.provider, r.prompt, r.priority FROM runs r WHERE r.state='queued' "
        "AND r.cancel=0 AND r.provider='command' AND (r.retry_at IS NULL OR r.retry_at<=?) "
        "AND NOT EXISTS (SELECT 1 FROM runs o WHERE o.thread_id=r.thread_id AND o.id<>r.id "
        "AND o.state IN ('starting', 'running'))", (db.now(),)).fetchall()
    judges = [r for r in rows if is_judge(r)]
    n = sum(weight(r) for r in judges if r["priority"] != "background")
    if any(r["priority"] == "background" for r in judges):
        from . import machine
        if machine.room()[0]:
            n += sum(weight(r) for r in judges if r["priority"] == "background")
    return n


def rank(conn: sqlite3.Connection, run: sqlite3.Row) -> int:
    """0 the train's check, 1 gate 2 (an item's gate2_run: its prompt is gate 1's), 2 any other."""
    if "eki.candidate" in (run["prompt"] or ""):
        return 0
    if conn.execute("SELECT 1 FROM items WHERE gate2_run=?", (run["id"],)).fetchone():
        return 1
    return 2


def first(conn: sqlite3.Connection, queued: list) -> list:
    """Queued runs in the order the engine tries them: judges first, by rank (stable),
    then every other run in the order it came."""
    judges = [r for r in queued if is_judge(r)]
    judges.sort(key=lambda r: rank(conn, r))
    return judges + [r for r in queued if not is_judge(r)]
