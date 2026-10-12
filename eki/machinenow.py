"""What the Mac is doing right now, for Settings → Machine's live line.

`now(conn)` reads it all fresh — items building across every project, test
runs holding a check slot, browsers holding a browser slot, the load, memory used and what is left of each
subscription — and `line(now)` says it in one sentence. Nothing is kept.
`EKI_MEMORY_TOTAL_GB` overrides the Mac's total memory and `EKI_MDS_STORES_GB` what
Spotlight's indexer holds (tests).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from typing import Any, Dict, List, Optional

from . import browserslots, checkslots, machine, machinecaps, providers, quota


def _total_gb() -> Optional[float]:
    forced = os.environ.get("EKI_MEMORY_TOTAL_GB")
    if forced:
        return float(forced)
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    if out.isdigit():
        return int(out) / 2 ** 30
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        return None


def memory() -> int:
    """Percent of memory in use: what isn't free (machine.free_gb) of the total."""
    total = _total_gb()
    if not total:
        return 0
    free = min(machine.free_gb(), total)
    return max(0, min(100, round(100 * (1 - free / total))))


def _mds_stores_gb(ps_out: str) -> Optional[float]:
    """The summed RSS (KB) of every process named exactly mds_stores in `ps -axo rss=,comm=`, in GB."""
    kb, seen = 0, False
    for row in ps_out.splitlines():
        rss, _, comm = row.strip().partition(" ")
        if rss.isdigit() and os.path.basename(comm.strip()) == "mds_stores":
            kb, seen = kb + int(rss), True
    return kb / 2 ** 20 if seen else None


def spotlight_gb() -> Optional[float]:
    """Resident memory of Spotlight's indexer (mds_stores) in GB; None when ps fails or none runs."""
    forced = os.environ.get("EKI_MDS_STORES_GB")
    if forced:
        return float(forced)
    try:
        r = subprocess.run(["ps", "-axo", "rss=,comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return _mds_stores_gb(r.stdout) if r.returncode == 0 else None


def _quota(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """What is left of each subscription with a reading, by its fullest window."""
    cfgs = providers.config()
    out = []
    for (name,) in conn.execute("SELECT provider FROM quota ORDER BY provider").fetchall():
        if name not in cfgs or cfgs[name].get("off"):
            continue
        w = quota.fullest(conn, name)
        if w is not None:
            out.append({"provider": name, "left": max(0, min(100, round(100 * (1 - float(w.get("used") or 0)))))})
    return out


def now(conn: sqlite3.Connection) -> Dict[str, Any]:
    agents = conn.execute("SELECT COUNT(*) FROM items WHERE state='building'").fetchone()[0]
    out: Dict[str, Any] = {"agents": int(agents), "checks": checkslots.busy(conn),
                           "browsers": browserslots.held(),
                           "load": round(os.getloadavg()[0], 1), "cores": os.cpu_count() or 1,
                           "memory": memory()}
    left = _quota(conn)
    if left:
        out["quota"] = left
    return out


def station(conn: sqlite3.Connection) -> Dict[str, Any]:
    """The Station page's machine line: what is in use beside how many may be at once."""
    n, caps = now(conn), machinecaps.settings()
    out = {"agents": n["agents"], "agents_cap": caps["agents"], "checks": n["checks"],
           "checks_cap": caps["checks"], "browsers": browserslots.held(), "browsers_cap": caps["browsers"],
           "load": n["load"], "cores": n["cores"]}
    gb = spotlight_gb()
    if gb is not None and gb > 2.0:
        out["spotlight_gb"] = round(gb, 1)
    return out


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def line(now: Dict[str, Any]) -> str:
    """now: 3 agents working · 1 test run · 2 browsers · load 9.2 on 18 cores · memory 64% · quota left claude 70%"""
    parts = [f"{_n(now['agents'], 'agent')} working", _n(now["checks"], "test run"),
             _n(now.get("browsers", 0), "browser"),
             f"load {now['load']:.1f} on {_n(now['cores'], 'core')}", f"memory {now['memory']}%"]
    if now.get("quota"):
        parts.append("quota left " + ", ".join(f"{q['provider']} {q['left']}%" for q in now["quota"]))
    return "now: " + " · ".join(parts)
