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
from typing import Any, Dict, List, Optional

#: Fraction of installed memory MLX will work with before macOS starts killing
#: things. The wired limit can't be raised without sudo, so treat this as the
#: real ceiling rather than the number on the box.
CEILING_FRACTION = 0.78


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
    #: 0 keeps it loaded
    idle_minutes: float = 30.0

    @property
    def running(self) -> bool:
        return port_open(self.port)


@dataclass
class Memory:
    total_gb: float
    ceiling_gb: float
    committed_gb: float             # what the running models are said to cost
    free_gb: float = field(default=0.0)


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

    def for_backend(self, backend_key: str) -> Optional[LocalModel]:
        return next((m for m in self.models.values()
                     if m.backend == backend_key and m.port), None)

    def can_start(self, key: str) -> bool:
        m = self.models.get(key)
        if m is None or not m.start:
            return False
        return not m.gb or self.committed_gb() + m.gb <= self.ceiling_gb

    def touch(self, key: str) -> None:
        self.last_used[key] = time.time()
        if key in self.started:
            self._save_started()

    async def reap_idle(self, now: Optional[float] = None) -> List[str]:
        """Stop eki-started models nobody has used for their idle window."""
        now = now or time.time()
        stopped = []
        for key in list(self.started):
            m = self.models.get(key)
            if m is None or not m.idle_minutes or not m.stop:
                continue
            if not m.running:
                self.started.discard(key)
                self._save_started()
                continue
            if now - self.last_used.get(key, now) >= m.idle_minutes * 60:
                await self.stop(key)
                stopped.append(key)
        return stopped

    def get(self, key: str) -> Optional[LocalModel]:
        return self.models.get(key)

    def committed_gb(self) -> float:
        return round(sum(m.gb for m in self.models.values() if m.running), 1)

    def memory(self) -> Memory:
        committed = self.committed_gb()
        return Memory(total_gb=total_memory_gb(), ceiling_gb=self.ceiling_gb,
                      committed_gb=committed,
                      free_gb=round(max(0.0, self.ceiling_gb - committed), 1))

    def describe(self) -> List[Dict[str, Any]]:
        return [{
            "key": m.key, "label": m.label, "kind": m.kind, "port": m.port,
            "gb": m.gb, "note": m.note, "backend": m.backend,
            "running": m.running, "idle_minutes": m.idle_minutes,
            "started_by_hub": m.key in self.started,
            "last_used": self.last_used.get(m.key),
            "can_start": bool(m.start) and not m.running,
            "blocked_by_memory": (not m.running
                                  and m.gb > 0
                                  and self.committed_gb() + m.gb > self.ceiling_gb),
        } for m in self.models.values()]

    # ---- control ---------------------------------------------------------

    async def start(self, key: str, force: bool = False) -> str:
        model = self.models.get(key)
        if model is None:
            raise KeyError(key)
        # two runs arriving at a stopped model start it once
        async with self._starting.setdefault(key, asyncio.Lock()):
            return await self._start(model, force)

    async def _start(self, model: LocalModel, force: bool) -> str:
        key = model.key
        if model.running:
            return f"{key} is already up on :{model.port}"
        if not model.start:
            return f"{key} has no start command configured"
        committed = self.committed_gb()
        if not force and model.gb and committed + model.gb > self.ceiling_gb:
            loaded = ", ".join(m.key for m in self.models.values() if m.running)
            return (f"not starting {key}: {model.gb}GB on top of {committed}GB "
                    f"({loaded}) passes the {self.ceiling_gb}GB ceiling — "
                    f"stop something first")
        await self._spawn(model.start)
        self.started.add(key)
        self.touch(key)
        self._save_started()
        for _ in range(int(model_start_timeout(model) / 0.5)):
            await asyncio.sleep(0.5)
            if model.running:
                return f"{key} is up on :{model.port}"
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
        ))
    return out
