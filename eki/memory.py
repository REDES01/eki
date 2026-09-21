# SPDX-License-Identifier: Apache-2.0
"""What this Mac actually has left, as opposed to what eki loaded.

Unified memory is the one resource every local model competes for, and it
competes with everything else too: Docker's VM, a Blender scene, a hundred
browser tabs. Counting only the weights eki started leaves the real question
unanswered, and macOS answers it for you by killing the model server.

So this reads the kernel's own view: pages the system can hand out at once
(free, inactive, speculative, purgeable) and its memory-status level, the
percentage macOS itself uses to decide when to start reclaiming. Both are
cheap to read and are sampled on demand, never cached for long.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PAGE = 16384
#: keep this much unclaimed when deciding whether a model fits: the OS, the
#: model's own context growth, and whatever the user opens next
RESERVE_GB = 4.0


@dataclass
class Snapshot:
    total_gb: float
    available_gb: float           # what could be handed out right now
    used_gb: float                # active + wired: what's really in use
    level: int                    # kern.memorystatus_level, 100 = nothing in use
    at: float = field(default_factory=time.time)

    @property
    def pressure(self) -> str:
        """The kernel's own bands: it starts reclaiming below about 20."""
        if self.level <= 10:
            return "critical"
        if self.level <= 25:
            return "warning"
        return "normal"

    @property
    def headroom_gb(self) -> float:
        return round(max(0.0, self.available_gb - RESERVE_GB), 1)


@dataclass
class Holder:
    name: str
    gb: float
    pid: int


def _sysctl(name: str) -> Optional[str]:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def snapshot() -> Snapshot:
    total = int(_sysctl("hw.memsize") or 0) / 1024**3
    level = int(_sysctl("kern.memorystatus_level") or 50)
    pages: Dict[str, int] = {}
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            m = re.match(r"(Pages [a-z ]+?):\s+(\d+)", line)
            if m:
                pages[m.group(1)] = int(m.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    gb = lambda key: pages.get(key, 0) * PAGE / 1024**3     # noqa: E731
    available = gb("Pages free") + gb("Pages inactive") + gb("Pages speculative") \
        + gb("Pages purgeable")
    used = gb("Pages active") + gb("Pages wired down")
    if not pages:                                            # no vm_stat: trust the level
        available = total * level / 100
        used = total - available
    return Snapshot(total_gb=round(total, 1), available_gb=round(available, 1),
                    used_gb=round(used, 1), level=level)


def holders(limit: int = 6, min_gb: float = 0.5) -> List[Holder]:
    """Who has the memory — the biggest processes by resident size, so the
    Models pane can say "Docker has 9 GB" instead of just "not enough"."""
    try:
        out = subprocess.run(["ps", "-Ao", "rss=,pid=,comm="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found: List[Holder] = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            gb, pid = int(parts[0]) / 1024**2, int(parts[1])
        except ValueError:
            continue
        if gb < min_gb:
            continue
        found.append(Holder(name=_short(parts[2]), gb=round(gb, 1), pid=pid))
    found.sort(key=lambda h: -h.gb)
    return found[:limit]


#: system helpers whose real names say nothing to anyone
NICKNAMES = {
    "com.apple.WebKit.WebContent": "web pages",
    "com.apple.WebKit.GPU": "web pages",
    "com.apple.WebKit.Networking": "web pages",
    "com.apple.Virtualization.VirtualMachine": "a virtual machine",
    "WindowServer": "the window server",
}


def _short(command: str) -> str:
    """"…/Docker.app/Contents/MacOS/Docker" → "Docker"; "python3.12 -m mlx_lm" stays."""
    m = re.search(r"/([^/]+)\.app/", command)
    if m:
        return NICKNAMES.get(m.group(1), m.group(1))
    name = command.rsplit("/", 1)[-1][:40]
    return NICKNAMES.get(name, name)


def grouped_holders(limit: int = 5) -> List[Holder]:
    """Holders folded by name: a browser is one line, not thirty."""
    by_name: Dict[str, Holder] = {}
    for h in holders(limit=200, min_gb=0.1):
        got = by_name.get(h.name)
        if got is None:
            by_name[h.name] = Holder(name=h.name, gb=h.gb, pid=h.pid)
        else:
            got.gb = round(got.gb + h.gb, 1)
    out = sorted(by_name.values(), key=lambda h: -h.gb)
    return [h for h in out if h.gb >= 0.5][:limit]
