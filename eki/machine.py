"""Does the Mac have room for background work right now?

Background runs start only when the Mac is on power, memory isn't under
pressure and the CPU isn't busy. Work you're waiting for (`now`) is never
held back. `EKI_MACHINE=ok|busy` overrides the reading (tests, the drill).
"""
from __future__ import annotations

import os
import platform
import re
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
    forced = os.environ.get("EKI_MEMORY_PRESSURE")
    if forced:
        return int(forced)
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


#: the vm_stat lines free_gb() counts
PAGES = ("Pages free:", "Pages inactive:", "Pages purgeable:", "Pages speculative:")


def free_gb() -> float:
    """Memory free for a new program, in GB: free + inactive + purgeable + speculative
    pages (macOS vm_stat), all of which the kernel hands out without swapping.
    `EKI_FREE_GB` overrides it (tests, the drill)."""
    forced = os.environ.get("EKI_FREE_GB")
    if forced:
        return float(forced)
    out = _run("vm_stat")
    if out:
        size = 4096
        pages = 0
        for line in out.splitlines():
            if "page size of" in line:
                size = int("".join(ch for ch in line.split("page size of")[1] if ch.isdigit()) or 4096)
            elif line.startswith(PAGES):
                pages += int(line.split(":")[1].strip().rstrip(".") or 0)
        if pages:
            return pages * size / 2 ** 30
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        return float("inf")


def swap_pct() -> int:
    """Percent of swap in use, from `sysctl -n vm.swapusage` (`total = 18432.00M  used =
    16896.00M  free = 1536.00M  (encrypted)`); 0 with no swap or no reading.
    `EKI_SWAP_PCT` overrides it (tests, the drill)."""
    forced = os.environ.get("EKI_SWAP_PCT")
    if forced:
        return int(float(forced))
    unit = {"": 1.0, "K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 ** 2}     # to MB
    got = {m.group(1): float(m.group(2)) * unit[m.group(3)]
           for m in re.finditer(r"(total|used)\s*=\s*([\d.]+)([KMGT]?)",
                                _run("sysctl", "-n", "vm.swapusage"))}
    if not got.get("total"):
        return 0
    return int(round(100 * got.get("used", 0.0) / got["total"]))
