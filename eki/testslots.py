"""Test slots: agents' own test runs share the machine's check slots.

A build turn's agent runs its tests too (bin/check, npm test, pytest) — on
every core, like a judge. `eki slot -- <cmd>` (eki/cli/slot.py) runs such a
test inside a hold: it waits until a check slot is free (checkslots.py:
running judge runs plus holds, against `machine.checks`), then takes one.
While a wide judge run waits for its slots, no new hold starts.
A hold is an flock on EKI_HOME/slots/<i>.lock, so it dies with its process
— a restart, a crash or a kill loses nothing and leaks no slot.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import random
import time
from pathlib import Path
from typing import Iterator, Optional

from . import checkslots, machinecaps, paths


def folder() -> Path:
    p = paths.home() / "slots"
    p.mkdir(exist_ok=True)
    return p


def _locks() -> list:
    return sorted(folder().glob("*.lock"))


def held() -> int:
    """Locks held right now by any process (one we can take isn't held)."""
    n = 0
    for p in _locks():
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
def hold(conn, wait: float = 1.0) -> Iterator[None]:
    """Wait for a free check slot, then keep it until the block ends."""
    fd: Optional[int] = None
    while fd is None:
        # a waiting wide judge (gate 2, the train's check) goes first: its slots stay free
        if checkslots.busy(conn) + checkslots.reserved(conn) < checkslots.limit():
            fd = _take()
            # two holders may have looked at once: the count with ours in it decides
            if checkslots.busy(conn) + checkslots.reserved(conn) > checkslots.limit():
                os.close(fd)
                fd = None
        if fd is None:
            time.sleep(wait * (0.5 + random.random()))
    try:
        yield
    finally:
        os.close(fd)
