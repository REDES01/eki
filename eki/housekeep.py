"""Housekeeping: the engine's duty pass over the journal and what is built from it.

Each step runs on its own — one that raises is written down as a fault and
the others still run: forget journal rows past 90 days (at most once an
hour), settle the build scores, turn repeated faults into items, let the
loop pick eki's next self-work when it is on and eki is idle
(eki/selfpick.py), open the next round of each standing goal (eki/standing.py), write the daily digest when it is due and give the
local model its patch notes and triage (eki/digestprose.py). Nothing here is state that matters: the
hour between prunes lives in memory, and losing it on a restart only means
an early prune.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Callable, List, Tuple

from . import db, digest, digestprose, faults, observe, score, selfpick, standing

log = logging.getLogger("eki.housekeep")

#: how long the journal keeps a row
KEEP_DAYS = 90
#: how often the journal is pruned
PRUNE_EVERY = 3600.0
_last_prune = [0.0]


def _prune(conn: sqlite3.Connection) -> List[str]:
    if time.time() - _last_prune[0] < PRUNE_EVERY:
        return []
    _last_prune[0] = time.time()
    gone = observe.prune(conn, KEEP_DAYS)
    return [f"journal: forgot {gone} rows older than {KEEP_DAYS} days"] if gone else []


def _digest(conn: sqlite3.Connection) -> List[str]:
    now = db.now()
    page = digest.tick(conn, now)
    if not page:
        return []
    since = digest.window_start(now)        # the window digest.write took (kept beside the page)
    rid = digestprose.start(conn, page, since, now)
    return [f"digest written: {page}"] + ([f"digest prose asked of the local model: run {rid}"] if rid else [])


STEPS: List[Tuple[str, Callable[[sqlite3.Connection], List[str]]]] = [
    ("prune", _prune),
    ("score", lambda conn: score.settle(conn)),
    ("faults", lambda conn: faults.tick(conn)),
    ("pick", lambda conn: selfpick.tick(conn)),
    ("standing", lambda conn: standing.tick(conn)),
    ("digest", _digest),
    ("digestprose", digestprose.tick),
]


def tick(conn: sqlite3.Connection) -> List[str]:
    """One pass; returns what it did, one line each, for the engine's log."""
    said: List[str] = []
    for name, step in STEPS:
        try:
            said.extend(step(conn) or [])
        except Exception:                                # noqa: BLE001 — one failing never stops the others
            log.exception("housekeeping step %s failed", name)
            observe.fault(conn, f"housekeep.{name}")
    return said
