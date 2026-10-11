"""Test slots: agents' own test runs share the machine's check slots.

A build turn's agent runs its tests too (bin/check, npm test, pytest) — on
every core, like a judge. `eki slot -- <cmd>` (eki/cli/slot.py) runs such a
test inside a hold: it waits until a check slot is free (checkslots.py:
running judge runs plus holds, against `machine.checks`), then takes one.
While a wide judge run waits for its slots, no new hold starts.
A hold is an flock on EKI_HOME/slots/<i>.lock, so it dies with its process
— a restart, a crash or a kill loses nothing and leaks no slot.

Checks go first: a hold that hasn't started takes a slot only while no
queued check could start (`checkslots.ahead`) — even one queued after the
hold asked. A hold that has its slot is never interrupted; the check takes
the next slot that frees. Why: holds poll about once a second and the engine
starts checks only on its tick, so holds won every freed slot (2026-10-11:
3 slots held back to back, 18 checks queued for 25 minutes). A hold waiting
longer than NOTICE says so once on stderr. While it waits it holds an flock
on slots/waiting/<pid>-<random>.lock, so `waiting()` counts waiting holds
across processes and `behind()` feeds the station's line "N test runs
waiting behind checks".
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

from . import checkslots, machinecaps, paths


def folder() -> Path:
    p = paths.home() / "slots"
    p.mkdir(exist_ok=True)
    return p


#: seconds a hold waits before it says why, once
NOTICE = 180
NOTE = ("eki slot: waiting {} min for a test slot — checks (the train, gate 2, gate 1) "
        "go first; still waiting")


def _waiters() -> Path:
    p = folder() / "waiting"
    p.mkdir(exist_ok=True)
    return p


def _locks() -> list:
    return sorted(folder().glob("*.lock"))


def held() -> int:
    """Slot locks held right now by any process (one we can take isn't held)."""
    return _count(_locks())


def waiting() -> int:
    """Holds waiting for a slot right now, in any process."""
    return _count(sorted(_waiters().glob("*.lock")))


def behind(conn: sqlite3.Connection) -> int:
    """Holds waiting because a queued check goes first (0 when no check is queued)."""
    return waiting() if checkslots.ahead(conn) > 0 else 0


def _count(paths: list) -> int:
    n = 0
    for p in paths:
        try:
            fd = os.open(p, os.O_RDWR)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            n += 1
        finally:
            os.close(fd)        # closing drops our probe's lock, if we got it
    return n


def _take() -> int:
    """Take a free lock (a new one if every one is held); its open fd holds it."""
    for i in range(machinecaps.MOST["checks"] * 2):
        fd = os.open(folder() / f"{i}.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
    raise RuntimeError("no free test slot lock")


@contextlib.contextmanager
def _waiter() -> Iterator[None]:
    """An flock under slots/waiting for as long as this hold waits."""
    p = _waiters() / f"{os.getpid()}-{random.getrandbits(32):08x}.lock"
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            p.unlink()
        os.close(fd)


def _free(conn) -> bool:
    # ahead counts a waiting wide judge (gate 2, the train's check) by its weight
    return checkslots.ahead(conn) == 0 and checkslots.busy(conn) < checkslots.limit()


@contextlib.contextmanager
def hold(conn, wait: float = 1.0, notice: float = NOTICE) -> Iterator[None]:
    """Wait until no queued check goes first and a check slot is free, then keep
    the slot until the block ends."""
    fd: Optional[int] = None
    start, told = time.monotonic(), False
    with _waiter():
        while fd is None:
            if _free(conn):
                fd = _take()
                # two holders may have looked at once: the count with ours in it decides
                if checkslots.busy(conn) > checkslots.limit():
                    os.close(fd)
                    fd = None
            if fd is None:
                waited = time.monotonic() - start
                if not told and waited > notice:
                    print(NOTE.format(max(1, round(waited / 60))), file=sys.stderr, flush=True)
                    told = True
                time.sleep(wait * (0.5 + random.random()))
    try:
        yield
    finally:
        os.close(fd)
