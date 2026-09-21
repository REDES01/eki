"""Codex (ChatGPT plan) limits, from the Codex CLI's own app-server.

`codex app-server` answers `account/rateLimits/read` for whoever is signed in
to the CLI. eki starts it read-only, does the JSON-RPC handshake, asks once,
and exits — it never sees a token.

Field names have moved between CLI versions (usedPercent / used_percent,
windowDurationMins / window_minutes), so parsing is deliberately loose and
windows are sorted into slots by their real length, not their names.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

from . import _shapes as sh
from .base import QuotaProvider, Reading, Window, label_for

SHORT = ("primary", "five_hour", "fiveHour", "5h", "session", "hourly")
LONG = ("secondary", "weekly", "seven_day", "sevenDay", "7d", "week")


class _W:
    """Minimal stand-in so _shapes.assign_slots can sort by length."""

    def __init__(self, used, resets_at, window_seconds):
        self.used, self.resets_at, self.window_seconds = used, resets_at, window_seconds


def parse(payload: Any, now: int) -> Reading:
    found: Dict[str, _W] = {}
    for slot, aliases, fallback in (("short", SHORT, 5 * 3600), ("long", LONG, 7 * 86400)):
        win = sh.find_window(payload, *aliases)
        if win is None:
            continue
        used = sh.utilization_of(win, scale="percent")      # app-server reports percent
        if used is None:
            continue
        found[slot] = _W(used, sh.resets_of(win, now),
                         sh.window_seconds_of(win) or fallback)
    slots = sh.assign_slots(found)
    windows = [
        Window(key=f"codex_{slot}",
               label=label_for(w.window_seconds, "5H" if slot == "short" else "WEEK"),
               used=w.used, resets_at=w.resets_at, window_seconds=w.window_seconds)
        for slot, w in sorted(slots.items(), key=lambda kv: kv[1].window_seconds or 0)
    ]
    if not windows:
        raise ValueError("codex app-server returned a shape eki doesn't recognise")
    return Reading("codex", windows=windows, observed_at=now)


async def query(cmd: List[str], settle_ms: int = 400, timeout: float = 25) -> Any:
    """initialize → initialized → (settle) → account/rateLimits/read."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    async def send(obj: Dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def read_id(want: int) -> Any:
        while True:
            line = await proc.stdout.readline()
            if not line:
                err = (await proc.stderr.read())[:200].decode(errors="replace")
                raise RuntimeError(f"app-server closed early: {err or 'no output'}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want:
                if "error" in msg:
                    raise RuntimeError(f"app-server error: {msg['error']}")
                return msg.get("result")

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "eki", "title": "eki",
                                              "version": "2.0.0"}}})
        await asyncio.wait_for(read_id(1), timeout=timeout)
        await send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        await asyncio.sleep(settle_ms / 1000.0)       # asking too early comes back empty
        await send({"jsonrpc": "2.0", "id": 2,
                    "method": "account/rateLimits/read", "params": {}})
        return await asyncio.wait_for(read_id(2), timeout=timeout)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


class CodexAppServer(QuotaProvider):
    min_interval = 120.0

    def __init__(self, key: str, options=None, binary: str = ""):
        super().__init__(key, options)
        self.binary = binary

    async def fetch(self) -> Reading:
        if not self.binary:
            return Reading(self.key, note="codex not found on PATH")
        payload = await query([self.binary, "-s", "read-only", "app-server"])
        reading = parse(payload, int(time.time()))
        reading.provider = self.key
        return reading
