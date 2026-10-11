"""Check slots: how many judge runs may run at once.

A judge run is a command run that checks a change — gate 1's bin/check,
gate 2's check in company, the docs check, the train's check before a swap,
a project's own check (eki/projectwork.py).
With the suite on every core, one check saturates the Mac: two fast checks
beat five crawling ones. So at most `machine.checks` (routing.json,
eki/machinecaps.py; default max(2, cores/6)) run at a time; the rest stay queued. This is the engine's
scheduling — the queue and self-work just queue their runs as before.
Agents' own test runs inside build turns take the same slots through
`eki slot --` (eki/testslots.py), so busy counts their holds too.
"""
from __future__ import annotations

import sqlite3

from . import machinecaps

#: what a judge run's command line carries
MARKS = ("bin/check", "eki.doccheck", "eki.candidate", "eki-judge")    # eki-judge: a project's check


def is_judge(run: sqlite3.Row) -> bool:
    return run["provider"] == "command" and any(m in (run["prompt"] or "") for m in MARKS)


def limit() -> int:
    return machinecaps.settings()["checks"]


def judging(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT provider, prompt FROM runs "
                        "WHERE state IN ('starting', 'running') AND provider='command'").fetchall()
    return sum(1 for r in rows if is_judge(r))


def busy(conn: sqlite3.Connection) -> int:
    """Slots in use: running judge runs plus agents' test runs holding one."""
    from . import testslots
    return judging(conn) + testslots.held()


def waiting(conn: sqlite3.Connection, run: sqlite3.Row) -> bool:
    """A queued judge run held back only for want of a check slot."""
    return run["state"] == "queued" and is_judge(run) and busy(conn) >= limit()
