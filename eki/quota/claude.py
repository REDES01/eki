"""Claude subscription limits, from what Claude Code itself reported.

Reads the file the status-line bridge keeps. That means the numbers are only
as fresh as your last interactive Claude Code session — and the reading says
how old it is rather than pretending otherwise. An honest gap is the price of
not touching Claude Code's credentials.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from . import claude_bridge
from .base import QuotaProvider, Reading, Window

WINDOWS = (
    ("five_hour", "5H", 5 * 3600, "window"),
    ("seven_day", "WEEK", 7 * 86400, "window"),
    # behind a Claude apps gateway: a spend limit, which can exceed 100%
    ("spend_limit", "SPEND", None, "credits"),
)


def parse(payload: Dict[str, Any], now: int) -> Reading:
    limits = payload.get("rate_limits") or {}
    windows = []
    for key, label, seconds, kind in WINDOWS:
        win = limits.get(key)
        if not isinstance(win, dict):
            continue
        pct = win.get("used_percentage")
        if not isinstance(pct, (int, float)):
            continue
        resets = win.get("resets_at")
        resets = int(resets) if isinstance(resets, (int, float)) else None
        if resets is not None and resets <= now and kind == "window":
            # the window rolled over since this was written: whatever it said
            # is about a period that has ended, so it isn't shown at all
            continue
        windows.append(Window(key=key, label=label,
                              used=float(pct) / 100.0,      # documented as 0..100
                              resets_at=resets, window_seconds=seconds, kind=kind))
    observed = int(payload.get("observed_at") or 0) or None
    note = ""
    if not windows:
        # Claude Code ran and said nothing about limits. That is what an API-key
        # login looks like, and what a subscription looks like before its first
        # reply of the session.
        note = ("Claude Code ran but reported no limits — subscription accounts "
                "report them after the first reply in an interactive session")
    return Reading("claude", windows=windows, observed_at=observed, note=note)


class ClaudeStatusLine(QuotaProvider):
    min_interval = 10.0          # a local file read; cheap

    async def fetch(self) -> Reading:
        payload = claude_bridge.reading()
        if payload is None:
            if not claude_bridge.installed():
                return Reading("claude", note="bridge not installed — turn it on in "
                                              "Usage to read Claude's limits")
            return Reading("claude", note="waiting for Claude Code to report limits "
                                          "(use it once interactively)")
        return parse(payload, int(time.time()))
