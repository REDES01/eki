# SPDX-License-Identifier: Apache-2.0
"""Claude subscription limits, from what Claude Code itself reported.

Reads the file the status-line bridge keeps. That means the numbers are only
as fresh as your last interactive Claude Code session — and the reading says
how old it is rather than pretending otherwise. An honest gap is the price of
not touching Claude Code's credentials.

Whatever Claude Code reports is shown. It sends a five-hour and a weekly
window for the account, and separate windows for particular models when a
plan has them — Opus, Fable — under keys like `seven_day_fable`. Those are
read by shape rather than from a list, so a limit hub has never heard of
still turns up as a meter with the right name on it.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from . import claude_bridge
from .base import QuotaProvider, Reading, Window

#: the window a key starts with: its label, its length, and what kind it is
SHAPES = (
    ("five_hour", "5H", 5 * 3600, "window"),
    ("seven_day", "WEEK", 7 * 86400, "window"),
    ("thirty_day", "MONTH", 30 * 86400, "window"),
    # behind a Claude apps gateway: a spend limit, which can exceed 100%
    ("spend_limit", "SPEND", None, "credits"),
    ("spend", "SPEND", None, "credits"),
    # usage credits: money spent past the plan, from /usage
    ("credits", "CREDITS", None, "credits"),
)


def describe(key: str) -> Tuple[str, Optional[int], str, bool]:
    """(label, window seconds, kind, is it account-wide) for a reported key."""
    for prefix, label, seconds, kind in SHAPES:
        if key == prefix:
            # money isn't a window: shown, but never one of the two meters
            return label, seconds, kind, kind == "window"
        if key.startswith(prefix + "_"):
            # a per-model limit: "seven_day_opus" reads as OPUS WEEK
            model = key[len(prefix) + 1:].replace("_", " ").upper()
            return f"{model} {label}", seconds, kind, False
    return key.replace("_", " ").upper(), None, "window", False


def parse(payload: Dict[str, Any], now: int) -> Reading:
    limits = payload.get("rate_limits") or {}
    windows = []
    for key, win in limits.items():
        if not isinstance(win, dict):
            continue
        pct = win.get("used_percentage")
        if not isinstance(pct, (int, float)):
            continue
        label, seconds, kind, primary = describe(key)
        resets = win.get("resets_at")
        resets = int(resets) if isinstance(resets, (int, float)) else None
        if resets is not None and resets <= now and kind == "window":
            # the window rolled over since this was written: whatever it said
            # is about a period that has ended, so it isn't shown at all
            continue
        windows.append(Window(key=key, label=label,
                              used=float(pct) / 100.0,      # documented as 0..100
                              resets_at=resets, window_seconds=seconds, kind=kind,
                              primary=primary, detail=str(win.get("detail") or "")))
    # account-wide first, then per-model windows, then money
    windows.sort(key=lambda w: (not w.primary, w.kind != "window", w.window_seconds or 0))
    observed = int(payload.get("observed_at") or 0) or None
    note = ""
    if not windows:
        # Claude Code ran and said nothing about limits. That is what an API-key
        # login looks like, and what a subscription looks like before its first
        # reply of the session.
        note = ("Claude Code ran but reported no limits — subscription accounts "
                "report them after the first reply in an interactive session")
    return Reading("claude", windows=windows, observed_at=observed, note=note)


def merge(usage: Optional[Reading], status: Optional[Reading]) -> Optional[Reading]:
    """One reading from two sources, each window from whichever saw it last.

    /usage (the probe) sees everything but only when asked; the status line
    sees just the session and week, but every time you use Claude Code. So a
    fresh status line updates those two, and the per-model allowances and
    credits stay as /usage last reported them.
    """
    readings = [r for r in (usage, status) if r is not None and r.windows]
    if not readings:
        return usage or status
    readings.sort(key=lambda r: r.observed_at or 0)     # oldest first; newest wins
    by_key: Dict[str, Window] = {}
    for r in readings:
        for w in r.windows:
            by_key[w.key] = w
    windows = sorted(by_key.values(), key=lambda w: (not w.primary, w.kind != "window",
                                                     w.window_seconds or 0))
    return Reading("claude", windows=windows,
                   observed_at=max(r.observed_at or 0 for r in readings) or None)


class ClaudeStatusLine(QuotaProvider):
    min_interval = 10.0          # local file reads; cheap

    async def fetch(self) -> Reading:
        from . import claude_probe
        now = int(time.time())
        usage = claude_probe.reading()
        status = claude_bridge.reading()
        merged = merge(parse(usage, now) if usage else None,
                       parse(status, now) if status else None)
        if merged is not None and (merged.windows or merged.note):
            return merged
        if not claude_bridge.installed():
            return Reading("claude", note="bridge not installed — turn it on in "
                                          "Usage to read Claude's limits")
        return Reading("claude", note="waiting for Claude Code to report limits — "
                                      "Refresh now reads them from /usage")
