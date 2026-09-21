# SPDX-License-Identifier: Apache-2.0
"""Local models: what's loaded, what it costs in memory, start and stop.

Unified memory is the scarce resource on this machine, not disk or cores. Two
large models loaded at once don't get slower — macOS kills one of them without
a word — so the only useful thing this module does is refuse to start a model
whose weights don't fit beside what's already running.

Liveness is decided by the port, never by process name: both servers are
`python …` under nohup, reparented to launchd, and no sensible pattern matches
them.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import memory

#: Fraction of installed memory MLX will work with before macOS starts killing
#: things. The wired limit can't be raised without sudo, so treat this as the
#: real ceiling rather than the number on the box.
CEILING_FRACTION = 0.78

#: how long an unused server stays loaded unless its provider says otherwise.
#: Loading is seconds; what an unload really costs is the prompt cache, so the
#: window is long enough that a conversation with pauses in it never pays that
DEFAULT_IDLE_MINUTES = 15.0

#: used more recently than this counts as "in the middle of something": only
#: a request the user is waiting on may unload it
RECENT_SECONDS = 180.0


@dataclass
class LocalModel:
    key: str
    label: str
    port: int
    start: str = ""
    stop: str = ""
    gb: float = 0.0                 # measured resident size once loaded
    note: str = ""
    backend: str = ""               # the backend key this serves, if any
    kind: str = "llm"
    #: stop it after this long unused, if eki was the one that started it;
    #: 0 pins it: the idle timer leaves it alone, and it is the last thing
    #: unloaded to make room
    idle_minutes: float = DEFAULT_IDLE_MINUTES

    @property
    def pinned(self) -> bool:
        return not self.idle_minutes

    @property
    def running(self) -> bool:
        return port_open(self.port)


@dataclass
class Memory:
    total_gb: float
    ceiling_gb: float
    committed_gb: float             # what the running models are said to cost
    free_gb: float = field(default=0.0)      # what a model could still take
    available_gb: float = 0.0       # the OS's view: what it could hand out now
    other_gb: float = 0.0           # in use by everything that isn't an eki model
    pressure: str = "normal"        # the kernel's band: normal | warning | critical
    holders: List[Dict[str, Any]] = field(default_factory=list)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


def default_ceiling_gb() -> float:
    """What this Mac can hold in weights, from what it has installed."""
    total = total_memory_gb()
    return round(total * CEILING_FRACTION, 1) if total else 24.0


def total_memory_gb() -> float:
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        return round(int(out.stdout.strip()) / 1024**3, 1)
    except Exception:                               # noqa: BLE001
        return 0.0


class ModelManager:
    #: which servers eki started, remembered across engine restarts — the
    #: engine gets restarted for its own reasons and that is no reason to
    #: leave a model loaded forever
    STARTED_FILE = Path("~/.eki/started.json").expanduser()

    def __init__(self, models: List[LocalModel], ceiling_gb: float = 0.0):
        self.models = {m.key: m for m in models}
        self.ceiling_gb = ceiling_gb or default_ceiling_gb()
        #: models eki itself brought up — the only ones it will unload on idle;
        #: a server the user started by hand is theirs to stop
        self.started: set = set()
        self.last_used: Dict[str, float] = {}
        self._starting: Dict[str, asyncio.Lock] = {}
        self._busy: Dict[str, int] = {}
        self._remember()

    def _remember(self) -> None:
        """Read back what a previous engine started, dropping what has stopped."""
        try:
            data = json.loads(self.STARTED_FILE.read_text())
        except (OSError, ValueError):
            return
        for key, when in (data or {}).items():
            model = self.models.get(key)
            if model is not None and model.running:
                self.started.add(key)
                self.last_used[key] = float(when)

    def _save_started(self) -> None:
        try:
            self.STARTED_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.STARTED_FILE.write_text(json.dumps(
                {k: self.last_used.get(k, time.time()) for k in self.started}))
        except OSError:
            pass

    def adopt(self, previous: "ModelManager") -> None:
        self.started = {k for k in previous.started if k in self.models}
        self.last_used = {k: v for k, v in previous.last_used.items() if k in self.models}
        # runs in flight keep their hold: a settings change mid-answer must
        # not leave the server they're using looking free to unload
        self._busy = {k: v for k, v in previous._busy.items() if k in self.models}
        self._starting = {k: v for k, v in previous._starting.items() if k in self.models}

    def for_backend(self, backend_key: str) -> Optional[LocalModel]:
        return next((m for m in self.models.values()
                     if m.backend == backend_key and m.port), None)

    def fits(self, m: LocalModel) -> Tuple[bool, str]:
        """Would loading this leave the Mac working? Two ceilings: what MLX
        will address, and what the OS actually has free right now."""
        if not m.gb:
            return True, ""
        committed = self.committed_gb()
        if committed + m.gb > self.ceiling_gb:
            loaded = ", ".join(x.key for x in self.models.values() if x.running) or "nothing"
            return False, (f"{m.gb} GB on top of {committed} GB ({loaded}) passes the "
                           f"{self.ceiling_gb} GB ceiling")
        snap = memory.snapshot()
        if m.gb > snap.headroom_gb:
            return False, (f"needs {m.gb} GB, the Mac has {snap.available_gb} GB available "
                           f"(keeping {memory.RESERVE_GB:g} back)")
        return True, ""

    def can_start(self, key: str, eager: bool = False) -> bool:
        """Startable now, or after unloading eki's own servers — the idle
        ones, or with `eager` any that no run is using."""
        m = self.models.get(key)
        if m is None or not m.start:
            return False
        if self.fits(m)[0]:
            return True
        reclaimable = sum(x.gb for x in self.evictable(eager) if x.key != key)
        if not reclaimable or not m.gb:
            return False
        snap = memory.snapshot()
        return (m.gb <= snap.headroom_gb + reclaimable
                and self.committed_gb() - reclaimable + m.gb <= self.ceiling_gb)

    def busy(self, key: str) -> bool:
        return self._busy.get(key, 0) > 0

    def hold(self, key: str) -> None:
        """A run is using this server: it must not be unloaded under it."""
        self._busy[key] = self._busy.get(key, 0) + 1

    def release(self, key: str) -> None:
        self._busy[key] = max(0, self._busy.get(key, 0) - 1)
        self.touch(key)

    def evictable(self, eager: bool = False) -> List[LocalModel]:
        """eki's own servers that may be unloaded to make room, in the order
        they would go. Never one a run is using, never one eki didn't start.

        Background work (measuring, waking the labeller) only takes servers
        that have sat unused a few minutes and aren't pinned. A request the
        user is waiting on (`eager`) may take the rest as well — a reload is
        seconds, and an image that can't be made because a chat model is
        resting in memory is worse — recently used ones next, pinned last.
        """
        now = time.time()
        ranked = []
        for k, m in self.models.items():
            if k not in self.started or not m.running or not m.stop or self.busy(k):
                continue
            recent = now - self.last_used.get(k, 0) < RECENT_SECONDS
            if (recent or m.pinned) and not eager:
                continue
            ranked.append(((m.pinned, recent, -m.gb), m))
        return [m for _, m in sorted(ranked, key=lambda pair: pair[0])]

    async def make_room(self, gb: float, eager: bool = False) -> List[str]:
        """Unload eki-started servers, in `evictable` order, until `gb` more
        would fit. Never touches anything eki didn't start."""
        stopped: List[str] = []
        for m in self.evictable(eager):
            snap = memory.snapshot()
            if gb <= snap.headroom_gb and self.committed_gb() + gb <= self.ceiling_gb:
                break
            await self.stop(m.key)
            stopped.append(m.key)
        return stopped

    def touch(self, key: str) -> None:
        self.last_used[key] = time.time()
        if key in self.started:
            self._save_started()

    async def reap_idle(self, now: Optional[float] = None,
                        pressure: Optional[str] = None) -> List[str]:
        """Stop eki-started models nobody has used for their idle window —
        or, when the Mac is under memory pressure, for two minutes."""
        now = now or time.time()
        if pressure is None:
            pressure = memory.snapshot().pressure
        stopped = []
        for key in list(self.started):
            m = self.models.get(key)
            if m is None or not m.stop or self.busy(key):
                continue
            if not m.running:
                self.started.discard(key)
                self._save_started()
                continue
            idle = now - self.last_used.get(key, now)
            limit = 120.0 if pressure != "normal" else m.idle_minutes * 60
            if (m.idle_minutes or pressure != "normal") and idle >= limit:
                await self.stop(key)
                stopped.append(key)
        return stopped

    def get(self, key: str) -> Optional[LocalModel]:
        return self.models.get(key)

    def committed_gb(self) -> float:
        return round(sum(m.gb for m in self.models.values() if m.running), 1)

    def memory(self) -> Memory:
        committed = self.committed_gb()
        snap = memory.snapshot()
        # a model can take the smaller of: what's under the ceiling, and what
        # the OS could actually hand out after the reserve
        free = min(self.ceiling_gb - committed, snap.headroom_gb)
        return Memory(total_gb=snap.total_gb or total_memory_gb(), ceiling_gb=self.ceiling_gb,
                      committed_gb=committed, free_gb=round(max(0.0, free), 1),
                      available_gb=snap.available_gb,
                      other_gb=round(max(0.0, snap.used_gb - committed), 1),
                      pressure=snap.pressure,
                      holders=[{"name": h.name, "gb": h.gb, "pid": h.pid}
                               for h in memory.grouped_holders()])

    def unloads_at(self, key: str) -> Optional[float]:
        """When the idle timer will stop this server, if it is going to."""
        m = self.models.get(key)
        if m is None or m.pinned or key not in self.started or not m.running or not m.stop:
            return None
        return self.last_used.get(key, time.time()) + m.idle_minutes * 60

    def describe(self) -> List[Dict[str, Any]]:
        return [{
            "key": m.key, "label": m.label, "kind": m.kind, "port": m.port,
            "gb": m.gb, "note": m.note, "backend": m.backend,
            "running": m.running, "idle_minutes": m.idle_minutes,
            "started_by_hub": m.key in self.started,
            "last_used": self.last_used.get(m.key),
            "pinned": m.pinned,
            "unloads_at": None if self.busy(m.key) else self.unloads_at(m.key),
            "can_start": bool(m.start) and not m.running,
            "blocked_by_memory": not m.running and m.gb > 0 and not self.fits(m)[0],
            "busy": self.busy(m.key),
        } for m in self.models.values()]

    # ---- control ---------------------------------------------------------

    async def start(self, key: str, force: bool = False, eager: bool = False) -> str:
        """Bring a server up. `eager`: someone is waiting on this, so servers
        used moments ago (and, last, pinned ones) may be unloaded for it."""
        model = self.models.get(key)
        if model is None:
            raise KeyError(key)
        # two runs arriving at a stopped model start it once
        async with self._starting.setdefault(key, asyncio.Lock()):
            return await self._start(model, force, eager)

    async def _start(self, model: LocalModel, force: bool, eager: bool = False) -> str:
        key = model.key
        if model.running:
            return f"{key} is already up on :{model.port}"
        if not model.start:
            return f"{key} has no start command configured"
        ok, why = self.fits(model)
        if not ok and not force:
            # make room from eki's own servers, never from anything else
            freed = await self.make_room(model.gb, eager)
            ok, why = self.fits(model)
            if not ok:
                return f"not starting {key}: {why} — stop something first"
            self._last_freed = freed
        await self._spawn(model.start)
        self.started.add(key)
        self.touch(key)
        self._save_started()
        freed = getattr(self, "_last_freed", [])
        self._last_freed = []
        for _ in range(int(model_start_timeout(model) / 0.5)):
            await asyncio.sleep(0.5)
            if model.running:
                note = f" (unloaded {', '.join(freed)} to make room)" if freed else ""
                return f"{key} is up on :{model.port}{note}"
        return f"started {key}, but nothing is listening on :{model.port} yet"

    async def stop(self, key: str) -> str:
        model = self.models.get(key)
        if model is None:
            raise KeyError(key)
        if not model.running:
            return f"{key} was not running"
        if not model.stop:
            return f"{key} has no stop command configured"
        await self._spawn(model.stop, wait=True)
        for _ in range(20):
            await asyncio.sleep(0.5)
            if not model.running:
                self.started.discard(key)
                self._save_started()
                return f"{key} stopped"
        return f"{key} is still listening on :{model.port}"

    @staticmethod
    async def _spawn(command: str, wait: bool = False) -> None:
        """Run a launch script detached, so the server outlives this process."""
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", os.path.expanduser(command),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)                 # survives the engine restarting
        if wait:
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()


def model_start_timeout(model: LocalModel) -> float:
    """Big weights take a while to page in; small ones shouldn't hold us up."""
    return 120.0 if model.gb >= 10 else 60.0


def from_config(entries: List[Dict[str, Any]]) -> List[LocalModel]:
    out: List[LocalModel] = []
    for entry in entries or []:
        out.append(LocalModel(
            key=entry["key"],
            label=entry.get("label", entry["key"]),
            port=int(entry.get("port", 0)),
            start=entry.get("start", ""),
            stop=entry.get("stop", ""),
            gb=float(entry.get("gb", 0)),
            note=entry.get("note", ""),
            backend=entry.get("backend", ""),
            kind=entry.get("kind", "llm"),
            idle_minutes=float(entry.get("idle_minutes", DEFAULT_IDLE_MINUTES) or 0),
        ))
    return out
