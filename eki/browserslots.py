"""Browser slots: how many Playwright browsers are open at once, across every run.

`machine.browsers` (machinecaps.py) caps them. The browser shim
(browsershim.py) holds a slot for as long as a browser executable runs.

The lock-file contract, for every user: a browser slot is
folder()/<i>.lock for i in 0..31, held by an exclusive fcntl.flock; the
holder writes its own pid as decimal text into the file (truncated first);
the lock dies with the process — a crash or a kill leaks no slot.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import List, Set

from . import machinecaps, paths

#: slot files are <i>.lock for i in range(SLOTS)
SLOTS = 32


def folder() -> Path:
    p = paths.home() / "browsers" / "slots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def limit() -> int:
    return machinecaps.settings()["browsers"]


def _held() -> List[Path]:
    """The slot files some process holds an flock on right now."""
    out = []
    for i in range(SLOTS):
        p = folder() / f"{i}.lock"
        try:
            fd = os.open(p, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            out.append(p)
        finally:
            os.close(fd)        # closing drops our probe's lock, if we got it
    return out


def held() -> int:
    """Browser slots held right now by any process."""
    return len(_held())


def pids() -> Set[int]:
    """The pids written in the held slot files."""
    out = set()
    for p in _held():
        try:
            out.add(int(p.read_text().strip()))
        except (OSError, ValueError):
            pass                # a holder that hasn't written its pid yet
    return out
