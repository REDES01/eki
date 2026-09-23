# SPDX-License-Identifier: Apache-2.0
"""Whether the machine has room for background work right now (ROADMAP, Stage 3).

Background pieces run whenever the machine can spare it — not only when
you're away. You can be typing; if there's memory, CPU and GPU to spare, the
work goes on. It steps back when something needs them:

- memory: a piece starts only if its model fits in what's free, and one in
  progress is cancelled (and redone later) if macOS reports memory pressure;
- CPU and GPU: eki's own model makes the GPU look busy while it works, so
  the check happens *between* pieces, when that model is idle — other apps
  (a game, a build, an export) using a lot means the next piece waits.

`when: away` is for anyone who'd rather it only ran with nobody at the
keyboard. Your own requests to eki always come first.

The subscription budget (`spare` mode) is checked here too: a subscription
only while you're under pace for the week, and never the last of a window.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from . import memory

#: other apps using more than this share of all cores means the next piece waits
CPU_BUSY = 0.6
#: or more than this much of the GPU (read while eki's model is idle)
GPU_BUSY = 0.35
#: with `when: away`, no keyboard or mouse for this long
AWAY_SECONDS = 300
#: `spare` never uses the last of any window
RESERVE = 0.30


@dataclass
class Gate:
    ok: bool
    why: str = ""


def _run(argv: List[str], timeout: float = 3.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def idle_seconds() -> Optional[float]:
    """Since the last key press or mouse move (macOS), or None if unknown."""
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', _run(["ioreg", "-c", "IOHIDSystem", "-d", "4"]))
    return int(m.group(1)) / 1e9 if m else None


def gpu_busy() -> Optional[float]:
    """The GPU's utilisation, 0..1 (Apple silicon), or None if unknown."""
    found = [int(v) for v in re.findall(r'"Device Utilization %"\s*=\s*(\d+)',
                                        _run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]))]
    return max(found) / 100 if found else None


def cpu_busy(exclude: Iterable[int] = ()) -> Optional[float]:
    """How much of all cores other processes are using, 0..1."""
    out = _run(["ps", "-A", "-o", "pid=,%cpu="])
    if not out:
        return None
    skip = set(int(p) for p in exclude if p)
    total = 0.0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, pct = int(parts[0]), float(parts[1])
        except ValueError:
            continue
        if pid not in skip:
            total += pct
    return total / (100.0 * (os.cpu_count() or 1))


def check(*, model_gb: float = 0.0, model_loaded: bool = True, when: str = "resources",
          exclude_pids: Iterable[int] = (), cpu_limit: float = CPU_BUSY,
          gpu_limit: float = GPU_BUSY, measure_gpu: bool = True) -> Gate:
    """May the next piece start? Checked between pieces."""
    if when == "away":
        idle = idle_seconds()
        if idle is not None and idle < AWAY_SECONDS:
            return Gate(False, f"you're here (input {int(idle)} s ago)")
    snap = memory.snapshot()
    if snap.pressure != "normal":
        return Gate(False, f"memory pressure is {snap.pressure}")
    if not model_loaded and model_gb and model_gb > snap.headroom_gb:
        return Gate(False, f"the model needs {model_gb:g} GB, {snap.headroom_gb:g} GB is free")
    cpu = cpu_busy(exclude_pids)
    if cpu is not None and cpu > cpu_limit:
        return Gate(False, f"other apps are using {round(cpu * 100)}% of the CPU")
    if measure_gpu:
        gpu = gpu_busy()
        if gpu is not None and gpu > gpu_limit:
            return Gate(False, f"the GPU is {round(gpu * 100)}% busy")
    return Gate(True)


def must_stop() -> Gate:
    """Checked while a piece is running: only memory can't wait."""
    snap = memory.snapshot()
    if snap.pressure != "normal":
        return Gate(False, f"memory pressure is {snap.pressure}")
    return Gate(True)


def spare_room(reading: Any, now: Optional[float] = None, reserve: float = RESERVE) -> Gate:
    """May background work spend this subscription? Only below the reserve in
    every window, and only while the week is being spent slower than it
    passes — so nothing you'd need is taken."""
    if reading is None or not getattr(reading, "windows", None):
        return Gate(False, "no reading of its usage")
    now = now or time.time()
    for w in reading.windows:
        if w.kind != "window" or not w.primary:
            continue
        if w.used >= 1.0 - reserve:
            return Gate(False, f"{w.label} at {round(w.used * 100)}% — the last "
                               f"{round(reserve * 100)}% is yours")
        if w.resets_at and w.window_seconds and w.window_seconds >= 86400:
            elapsed = 1.0 - max(0.0, w.resets_at - now) / w.window_seconds
            if w.used > elapsed:
                return Gate(False, f"{w.label} is ahead of pace ({round(w.used * 100)}% used, "
                                   f"{round(elapsed * 100)}% of it gone)")
    return Gate(True)


class Awake:
    """Keeps the Mac from idle-sleeping while there's work (the screen may
    sleep): `caffeinate -i`, renewed in short leases so it can't outlive eki."""

    LEASE = 180

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.until = 0.0

    def hold(self) -> None:
        if self.proc is not None and self.proc.poll() is None and time.time() < self.until - 60:
            return
        try:
            self.proc = subprocess.Popen(["caffeinate", "-i", "-t", str(self.LEASE)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.until = time.time() + self.LEASE
        except OSError:
            self.proc = None

    def let_go(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
