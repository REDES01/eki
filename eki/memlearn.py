"""Memory learning: measure what each kind of work takes, and fold it into its estimate.

A housekeeping step (every pass). It reads the process table once and
measures the RSS of each process tree (a process and all its descendants):
each starting or running agent or judge run (from its worker pid and its
program's pid), each browser slot holder (eki/browserslots.py) and each test
slot holder (the pid written in a held slots/<i>.lock, eki/testslots.py). A
run's tree leaves out the subtrees of slot holders under it — those are
measured as their own kinds, "browser" and "check".

The peak so far of each tree lives in `mem_peaks`, so a restart loses
nothing. When a run is no longer starting/running, or a holder's pid is gone,
its peak is folded into its kind's estimate (memadmit.learn) and the row
deleted. eki/memadmit.py admits new work by those estimates.
"""
from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
import subprocess
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import browserslots, memadmit, testslots

log = logging.getLogger("eki.memlearn")

KB_PER_GB = 1024 * 1024


def _ps() -> str:
    """The process table: pid, ppid and RSS in KB, one process a line."""
    return subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True,
                          check=True, timeout=10).stdout


def _table(text: str) -> Tuple[Dict[int, int], Dict[int, List[int]]]:
    """RSS in KB by pid, and the children of each pid."""
    rss: Dict[int, int] = {}
    kids: Dict[int, List[int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            pid, ppid, kb = (int(p) for p in parts)
        except ValueError:
            continue
        rss[pid] = kb
        kids.setdefault(ppid, []).append(pid)
    return rss, kids


def _tree(roots: Iterable[Optional[int]], kids: Dict[int, List[int]], rss: Dict[int, int],
          skip: Set[int]) -> Set[int]:
    """The live pids under the roots, roots included, without the subtrees rooted at `skip`."""
    seen: Set[int] = set()
    todo = [r for r in roots if r and r in rss and r not in skip]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo.extend(k for k in kids.get(p, ()) if k not in skip and k not in seen)
    return seen


def _gb(pids: Set[int], rss: Dict[int, int]) -> float:
    return sum(rss.get(p, 0) for p in pids) / KB_PER_GB


def _test_holders() -> Set[int]:
    """The pids written in the test slot files some process holds."""
    out = set()
    for p in sorted(testslots.folder().glob("*.lock")):
        try:
            fd = os.open(p, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            continue                    # we got it: no one holds it
        except OSError:
            pass
        finally:
            os.close(fd)                # closing drops our probe's lock, if we got it
        try:
            out.add(int(p.read_text().strip()))
        except (OSError, ValueError):
            pass                        # a holder that hasn't written its pid yet
    return out


def _samples(conn: sqlite3.Connection, rss: Dict[int, int],
             kids: Dict[int, List[int]]) -> List[Tuple[str, str, float]]:
    """(key, kind, GB) of every tree measured now."""
    browsers, checks = browserslots.pids(), _test_holders()
    holders = browsers | checks
    out = []
    for kind, pids, prefix in (("browser", browsers, "browser"), ("check", checks, "hold")):
        for pid in pids:
            if pid in rss:
                out.append((f"{prefix}:{pid}", kind, _gb(_tree([pid], kids, rss, set()), rss)))
    for run in conn.execute("SELECT * FROM runs WHERE state IN ('starting', 'running') "
                            "AND pid IS NOT NULL"):
        kind = memadmit.run_kind(run)
        if not kind:
            continue
        tree = _tree([run["pid"], run["child_pid"]], kids, rss, holders)
        if tree:
            out.append((f"run:{run['id']}", kind, _gb(tree, rss)))
    return out


def _done(conn: sqlite3.Connection, key: str, rss: Dict[int, int]) -> bool:
    what, _, ref = key.partition(":")
    if what == "run":
        row = conn.execute("SELECT state FROM runs WHERE id=?", (ref,)).fetchone()
        return row is None or row[0] not in ("starting", "running")
    try:
        return int(ref) not in rss
    except ValueError:
        return True


def tick(conn: sqlite3.Connection) -> List[str]:
    """Measure every tree, keep each one's peak, and learn from those that ended."""
    try:
        rss, kids = _table(_ps())
    except (OSError, subprocess.SubprocessError) as e:
        log.info("process table not read: %s", e)
        return []
    if not rss:
        return []                       # an empty table is no reading: every pid would look gone
    now = time.time()
    for key, kind, gb in _samples(conn, rss, kids):
        conn.execute("INSERT INTO mem_peaks (key, kind, peak_gb, at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET peak_gb=MAX(peak_gb, excluded.peak_gb), "
                     "at=excluded.at", (key, kind, gb, now))
    said = []
    for key, kind, peak in conn.execute("SELECT key, kind, peak_gb FROM mem_peaks").fetchall():
        if not _done(conn, key, rss):
            continue
        before = conn.execute("SELECT gb, samples FROM mem_estimates WHERE kind=?", (kind,)).fetchone()
        gb = memadmit.learn(conn, kind, peak)
        conn.execute("DELETE FROM mem_peaks WHERE key=?", (key,))
        after = conn.execute("SELECT gb, samples FROM mem_estimates WHERE kind=?", (kind,)).fetchone()
        if after and (before is None or tuple(before) != tuple(after)):
            said.append(f"memory: {kind} {gb:.1f} GB ({after[1]} samples)")
    return said
