# SPDX-License-Identifier: Apache-2.0
"""Whether the machine has room for background work right now (ROADMAP, Stage 3).

Background pieces run whenever the machine can spare it — not only when
you're away. You can be typing; if there's memory, CPU and GPU to spare, the
work goes on. It steps back when something needs them:

- memory: a piece starts only if its model fits in what's free, and one in
  progress is cancelled (and redone later) if macOS reports memory pressure;
- CPU and GPU: what *other* processes use — eki's own model servers are
  left out (the GPU from each Metal client's own counter), checked between
  pieces — a game, a build or an export using a lot means the next piece waits;
- power: on a laptop, only while it's plugged in (a piece in progress stops
  when it's unplugged).

`when: away` is for anyone who'd rather it only ran with nobody at the
keyboard. Your own requests to eki always come first.

A goal that uses the screen runs only while you're away, whatever the mode:
the screen is one, and it's yours. Its own clicks and keys would look like
someone at the keyboard, so eki's screen tools leave a mark when they move
anything (`note_input`), and `Presence` tells your input from theirs — so it
steps out the moment you're back.

The subscription budget (`spare` mode) is checked here too: a subscription
only while you're under pace for the week, and never the last of a window.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
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
#: when eki's screen tools last clicked, typed or scrolled — written by
#: whichever process drove the screen (the engine, or `eki mcp` under Codex)
INPUT_MARK = Path("~/.eki/run/input-at").expanduser()


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


def screen_locked() -> Optional[bool]:
    """Whether the login window is over the screen (macOS); None if unknown."""
    out = _run(["ioreg", "-n", "Root", "-d1"])
    if "IOConsoleUsers" not in out:
        return None
    return bool(re.search(r'"CGSSessionScreenIsLocked"\s*=\s*Yes', out))


def note_input(now: Optional[float] = None) -> None:
    """eki's screen tools just moved something: not you."""
    try:
        INPUT_MARK.parent.mkdir(parents=True, exist_ok=True)
        INPUT_MARK.write_text(str(now or time.time()))
    except OSError:
        pass


def eki_input_at() -> float:
    try:
        return float(INPUT_MARK.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0.0


class Presence:
    """When you last touched the keyboard or mouse, eki's own input left out.

    macOS knows only when the last input was, anyone's. An input more than a
    moment after eki's screen tools last acted is yours; one before that
    could be either, so what was known stands."""

    GRACE = 1.5

    def __init__(self) -> None:
        self.seen_at: Optional[float] = None

    def update(self, idle: Optional[float], eki_at: float, now: Optional[float] = None) -> Optional[float]:
        """Seconds since your last input, or None if unknown."""
        now = now or time.time()
        if idle is None:
            return None
        latest = now - idle
        if latest > eki_at + self.GRACE or self.seen_at is None:
            # yours — or, not having watched before, taken to be
            self.seen_at = latest
        return max(0.0, now - self.seen_at)

    def since(self, t: float) -> bool:
        """You've touched the Mac since `t`."""
        return self.seen_at is not None and self.seen_at > t

    def read(self) -> Optional[float]:
        return self.update(idle_seconds(), eki_input_at())


def gpu_busy() -> Optional[float]:
    """The GPU's utilisation, 0..1 (Apple silicon), or None if unknown."""
    found = [int(v) for v in re.findall(r'"Device Utilization %"\s*=\s*(\d+)',
                                        _run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]))]
    return max(found) / 100 if found else None


def gpu_times() -> Optional[dict]:
    """{pid: GPU nanoseconds so far}, from each Metal client's own counter
    (no admin rights needed), or None where the machine doesn't say."""
    out = _run(["ioreg", "-r", "-c", "AGXDeviceUserClient", "-l"], timeout=5)
    if "IOUserClientCreator" not in out:
        return None
    times: dict = {}
    for entry in out.split("+-o ")[1:]:
        who = re.search(r'"IOUserClientCreator"\s*=\s*"pid (\d+)', entry)
        if not who:
            continue
        spent = sum(int(v) for v in re.findall(r'"accumulatedGPUTime"=(\d+)', entry))
        pid = int(who.group(1))
        times[pid] = times.get(pid, 0) + spent
    return times


def gpu_busy_others(exclude: Iterable[int] = (), interval: float = 1.0) -> Optional[float]:
    """How much of the GPU other processes used over the last `interval`,
    0..1 — eki's own model left out, so its work never makes it wait."""
    skip = set(int(p) for p in exclude if p)
    first = gpu_times()
    if first is None:
        return None
    t0 = time.monotonic()
    time.sleep(interval)
    second = gpu_times() or {}
    wall = (time.monotonic() - t0) * 1e9
    used = sum(max(0, n - first.get(pid, n)) for pid, n in second.items() if pid not in skip)
    return min(1.0, used / wall) if wall > 0 else None


def on_battery() -> Optional[bool]:
    """True on battery, False on AC power, None where there's no battery to ask."""
    out = _run(["pmset", "-g", "batt"])
    if "Battery Power" in out:
        return True
    if "AC Power" in out:
        return False
    return None


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
          gpu_limit: float = GPU_BUSY, measure_gpu: bool = True,
          on_battery_ok: bool = False, idle: Optional[float] = None) -> Gate:
    """May the next piece start? Checked between pieces. `idle`: seconds
    since your last input, eki's own left out (Presence), if known."""
    if not on_battery_ok and on_battery():
        return Gate(False, "on battery — waiting for power")
    if when == "away":
        idle = idle if idle is not None else idle_seconds()
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
        gpu = gpu_busy_others(exclude_pids)
        if gpu is None:
            gpu = gpu_busy()                    # no per-app counters: the whole GPU
        if gpu is not None and gpu > gpu_limit:
            return Gate(False, f"the GPU is {round(gpu * 100)}% busy")
    return Gate(True)


def must_stop(on_battery_ok: bool = False) -> Gate:
    """Checked while a piece is running: memory can't wait, nor a battery."""
    if not on_battery_ok and on_battery():
        return Gate(False, "on battery — waiting for power")
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
        self.display = False

    def hold(self, display: bool = False) -> None:
        """`display`: the screen stays on too — for a goal that uses it."""
        if self.proc is not None and self.proc.poll() is None and time.time() < self.until - 60 \
                and self.display == display:
            return
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.display = display
        try:
            self.proc = subprocess.Popen(["caffeinate", "-di" if display else "-i", "-t", str(self.LEASE)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.until = time.time() + self.LEASE
        except OSError:
            self.proc = None

    def let_go(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
