"""Does the Mac have room for background work right now?

Background runs start only when the Mac is on power, memory isn't under
pressure and the CPU isn't busy. Work you're waiting for (`now`) is never
held back. `EKI_MACHINE=ok|busy` overrides the reading (tests, the drill).
"""
from __future__ import annotations

import os
import platform
import subprocess
from typing import Tuple


def _run(*argv: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def on_power() -> bool:
    out = _run("pmset", "-g", "batt")
    return (not out) or "AC Power" in out       # a desktop Mac has no battery line to read


def memory_pressure() -> int:
    """1 normal, 2 warning, 4 critical (the kernel's own level)."""
    out = _run("sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip()
    return int(out) if out.isdigit() else 1


def cpu_busy() -> bool:
    load1, _, _ = os.getloadavg()
    return load1 > (os.cpu_count() or 4) * 0.75


def room() -> Tuple[bool, str]:
    forced = os.environ.get("EKI_MACHINE")
    if forced:
        return (forced == "ok"), f"EKI_MACHINE={forced}"
    if platform.system() != "Darwin":
        return True, "not a Mac: no gate"
    if not on_power():
        return False, "on battery"
    if memory_pressure() > 1:
        return False, "memory under pressure"
    if cpu_busy():
        return False, "CPU busy"
    return True, "room"
