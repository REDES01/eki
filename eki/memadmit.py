"""Memory admission: may new work start, given the memory the Mac has left?

Before an agent, a test run or a browser starts, its scheduler asks here.
The reading is free + reclaimable memory and swap in use (eki/machine.py);
below `machine.floor_gb` or above `machine.swap_max` (eki/machinecaps.py) new
work waits, and the answer says why: 'waiting for memory (x GB free, swap y%)'.
Each kind of work has an estimate, learned from the measured peak RSS of its
process tree (eki/memlearn.py folds peaks in through `learn`), and is admitted
only if it fits above the floor. Work started under GRACE seconds ago counts
its estimate, since the reading doesn't show it yet.

Running work is never stopped: this module only answers whether new work may
start. Nothing is kept between calls; the estimates live in `mem_estimates`.

Kinds are strings: "browser", "check", "agent:<provider>" (e.g. "agent:claude").
Why (2026-10-12): load 110, swap 16.5 of 18 GB with twenty headless game
browsers at ~1 GB each plus 22 agents, all admitted by counts alone.
"""
from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import browserslots, checkslots, machine, machinecaps, testslots

#: GB each kind needs before anything is learned
DEFAULTS = {"browser": 1.1, "check": 1.5, "agent": 0.3}
#: seconds after a start during which the reading doesn't show the work yet
GRACE = 60
#: peaks under this many GB are no measurement
LEAST = 0.01


def run_kind(run: sqlite3.Row) -> Optional[str]:
    """"check" for a judge run, None for any other command or component run, else "agent:<provider>"."""
    if checkslots.is_judge(run):
        return "check"
    prov = run["provider"]
    if not prov or prov in ("command", "component"):
        return None
    return f"agent:{prov}"


def estimate(conn: sqlite3.Connection, kind: str) -> float:
    """GB the kind is expected to need: learned, else its default. The bare "agent" is the
    largest learned agent:* estimate."""
    if kind == "agent":
        got = conn.execute("SELECT MAX(gb) FROM mem_estimates WHERE kind LIKE 'agent:%'").fetchone()[0]
        return float(got) if got is not None else DEFAULTS["agent"]
    row = conn.execute("SELECT gb FROM mem_estimates WHERE kind=?", (kind,)).fetchone()
    if row:
        return float(row[0])
    if kind.startswith("agent:"):
        return DEFAULTS["agent"]
    return DEFAULTS.get(kind, DEFAULTS["agent"])


def estimates(conn: sqlite3.Connection) -> List[Dict]:
    """Every learned estimate plus each default kind with none, sorted by kind."""
    out = {r[0]: {"kind": r[0], "gb": float(r[1]), "samples": int(r[2]), "learned": True}
           for r in conn.execute("SELECT kind, gb, samples FROM mem_estimates")}
    for kind, gb in DEFAULTS.items():
        out.setdefault(kind, {"kind": kind, "gb": gb, "samples": 0, "learned": False})
    return [out[k] for k in sorted(out)]


def learn(conn: sqlite3.Connection, kind: str, peak_gb: float) -> float:
    """Fold a measured peak into the kind's estimate: the first sets it, then 0.7 old + 0.3 peak."""
    row = conn.execute("SELECT gb, samples FROM mem_estimates WHERE kind=?", (kind,)).fetchone()
    if peak_gb < LEAST:
        return float(row[0]) if row else estimate(conn, kind)
    gb, n = (0.7 * float(row[0]) + 0.3 * peak_gb, int(row[1]) + 1) if row else (float(peak_gb), 1)
    conn.execute("INSERT INTO mem_estimates (kind, gb, samples, at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(kind) DO UPDATE SET gb=excluded.gb, samples=excluded.samples, "
                 "at=excluded.at", (kind, gb, n, time.time()))
    return gb


def reading() -> Dict:
    return {"free_gb": round(machine.free_gb(), 1), "swap_pct": machine.swap_pct()}


def why(r: Dict) -> str:
    return f"waiting for memory ({r['free_gb']:.1f} GB free, swap {r['swap_pct']}%)"


def low(r: Optional[Dict] = None) -> Optional[str]:
    """why(r) when the Mac is below the floor or above the swap limit, else None."""
    r = r if r is not None else reading()
    caps = machinecaps.settings()
    if r["free_gb"] < caps["floor_gb"] or r["swap_pct"] > caps["swap_max"]:
        return why(r)
    return None


def _fresh_held(paths: Iterable[Path], since: float) -> int:
    """Slot files some process holds an flock on, touched since `since`."""
    n = 0
    for p in paths:
        try:
            if p.stat().st_mtime < since:
                continue
            fd = os.open(p, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            n += 1
        finally:
            os.close(fd)        # closing drops our probe's lock, if we got it
    return n


def reserved(conn: sqlite3.Connection) -> float:
    """GB of work started under GRACE seconds ago, which the reading doesn't show yet."""
    since = time.time() - GRACE
    total = 0.0
    for run in conn.execute("SELECT * FROM runs WHERE state IN ('starting', 'running') "
                            "AND spawned_at >= ?", (since,)):
        kind = run_kind(run)
        if kind:
            total += estimate(conn, kind)
    browsers = _fresh_held((browserslots.folder() / f"{i}.lock" for i in range(browserslots.SLOTS)), since)
    checks = _fresh_held(sorted(testslots.folder().glob("*.lock")), since)
    return total + browsers * estimate(conn, "browser") + checks * estimate(conn, "check")


class Tick:
    """One reading and one reserved() for an engine or scheduler pass."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.reading = reading()
        self.reserved = reserved(conn)
        self.started = 0.0
        self.caps = machinecaps.settings()

    def fits(self, kind: str) -> Optional[str]:
        """None when work of this kind may start now, else why it waits."""
        r = self.reading
        if r["swap_pct"] > self.caps["swap_max"]:
            return why(r)
        left = r["free_gb"] - self.reserved - self.started - estimate(self.conn, kind)
        return None if left >= self.caps["floor_gb"] - 1e-9 else why(r)      # 7.1 - 1.1 is 5.999…

    def took(self, kind: str) -> None:
        """Work of this kind started this pass."""
        self.started += estimate(self.conn, kind)


def fits(conn: sqlite3.Connection, kind: str) -> Optional[str]:
    return Tick(conn).fits(kind)
