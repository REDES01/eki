"""The words of a queue wait: which runs hold the slots, and the machine's browser and check slots.

`access.gate` counts the runs taking a card's slots; this says who they are, so a wait can be
checked by hand ("runs a1 pid 123"). A count that can't be right — more busy than slots, or no
counted run alive — is never shown as a number: it is written down as an `access.slots` fault
and the agent is told the slots are being rechecked.
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional, Tuple

from .. import browserslots, checkslots, observe

RECHECK = "rechecking slots"


def _alive(run) -> Optional[bool]:
    """True/False for a run with a pid; None for one that has none yet (still spawning)."""
    pid = run["pid"]
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True             # someone else's process: alive
    except (OSError, ValueError):
        return False
    return True


def _held(runs: List[sqlite3.Row]) -> str:
    return ", ".join(f"{r['id']} pid {r['pid'] or '-'}" for r in runs)


def _machine(conn: sqlite3.Connection) -> str:
    parts = []
    try:
        pids = sorted(browserslots.pids())
        b = f"browsers {browserslots.held()} of {browserslots.limit()}"
        parts.append(b + (f" (pid {', '.join(map(str, pids))})" if pids else ""))
    except Exception:
        pass
    try:
        parts.append(f"checks {checkslots.busy(conn)} of {checkslots.limit()}")
    except Exception:
        pass
    return "".join(f" · {p}" for p in parts)


def why(conn: sqlite3.Connection, name: str, slots: int, runs: List[sqlite3.Row],
        before: str = "") -> Tuple[str, bool]:
    """(the wait's words after "waits for <name>: ", whether the count is wrong).

    A wrong count is written as an `access.slots` fault, once while the wait's words stay
    the same (`before` is the run's current why)."""
    busy = len(runs)
    alive = [_alive(r) for r in runs]
    wrong = busy > slots or (busy > 0 and not any(a is not False for a in alive))
    if wrong:
        if not before.endswith(RECHECK):
            dead = [r["id"] for r, a in zip(runs, alive) if a is False]
            observe.fault(conn, "access.slots",
                          f"{name}: {busy} counted for {slots} slot{'s' if slots != 1 else ''}; "
                          f"runs {_held(runs)}; "
                          f"no live pid: {', '.join(dead) or 'none'}")
        return RECHECK, True
    held = _held(runs)
    return (f"{busy} of {slots} slot{'s' if slots > 1 else ''} busy"
            + (f" (runs {held})" if held else "") + _machine(conn)), False
