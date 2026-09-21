# SPDX-License-Identifier: Apache-2.0
"""Read Claude Code's own /usage panel.

This is the complete picture — the session and weekly windows, a separate
weekly allowance per model where a plan has one (Fable, say), and the usage
credits you've spent past your plan — and it's free: /usage asks Anthropic
about your account without asking a model anything.

What comes back is a rendered terminal screen, not data, so this module is
two halves: `parse()` turns the text of that screen into limits, and the
probe (claude_probe.py) drives a session to put the panel on screen. Parsing
is by shape — a title, a bar with "NN% used", a "Resets …" line — so a new
per-model block turns up without a code change.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_PCT = re.compile(r"(\d+(?:\.\d+)?)%\s*used")
_RESETS = re.compile(r"Resets\s+(.+?)(?:\s*\(([^)]+)\))?\s*$")
_MONEY = re.compile(r"(\$[\d,]+(?:\.\d+)?)\s*/\s*(\$[\d,]+(?:\.\d+)?)\s*spent")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class Block:
    key: str                 # five_hour, seven_day, seven_day_fable, credits
    title: str               # as Claude Code wrote it
    used: float              # percent, 0..100
    resets_at: Optional[int]
    resets_text: str
    detail: str = ""         # e.g. "$113.74 / $120.00"


def key_for(title: str) -> Optional[str]:
    t = title.strip().lower()
    if t == "current session":
        return "five_hour"
    if t in ("current week (all models)", "current week"):
        return "seven_day"
    m = re.fullmatch(r"current week \((.+)\)", t)
    if m:
        model = re.sub(r"[^a-z0-9]+", "_", m.group(1).replace(" only", "")).strip("_")
        return f"seven_day_{model}"
    if t.startswith("usage credits") or t.startswith("extra usage"):
        return "credits"
    return None


def parse(lines: List[str], now: Optional[float] = None) -> List[Block]:
    """Blocks from the text of a rendered /usage panel, top to bottom."""
    now = now or time.time()
    clean = [l.strip() for l in lines]
    blocks: List[Block] = []
    for i, line in enumerate(clean):
        key = key_for(line)
        if key is None:
            continue
        # the bar and the reset line follow the title within a few lines
        window = clean[i + 1:i + 4]
        pct = next((_PCT.search(l) for l in window if _PCT.search(l)), None)
        if pct is None:
            continue
        reset_line = next((l for l in window if "Resets" in l), "")
        money = _MONEY.search(reset_line) or next(
            (_MONEY.search(l) for l in window if _MONEY.search(l)), None)
        resets_text, resets_at = "", None
        m = _RESETS.search(reset_line.split("·")[-1].strip())
        if m:
            resets_text = m.group(1).strip()
            resets_at = when(resets_text, m.group(2), now)
        blocks.append(Block(key=key, title=line, used=float(pct.group(1)),
                            resets_at=resets_at, resets_text=resets_text,
                            detail=f"{money.group(1)} / {money.group(2)}" if money else ""))
    return blocks


def when(text: str, zone: Optional[str], now: float) -> Optional[int]:
    """Epoch seconds for "4:10pm", "Sep 21 at 11pm" or "Oct 1", in `zone`.

    Claude Code prints the next reset in the viewer's own zone, and says
    which. A date without a year is the next one to come; a time without a
    date is today's, or tomorrow's if today's has passed.
    """
    try:
        tz = ZoneInfo(zone) if zone else None
    except ZoneInfoNotFoundError:
        tz = None
    base = datetime.fromtimestamp(now, tz)
    text = text.strip().lower().replace(".", "")
    date_part, _, time_part = text.partition(" at ")
    clock = None
    if re.fullmatch(r"\d{1,2}(:\d{2})?\s*(am|pm)", date_part):
        clock, date_part = date_part, ""
    elif time_part:
        clock = time_part

    hour, minute = 0, 0
    if clock:
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", clock.strip())
        if not m:
            return None
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        minute = int(m.group(2) or 0)

    if date_part:
        m = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{1,2})(?:,?\s*(\d{4}))?", date_part.strip())
        if not m or m.group(1) not in _MONTHS:
            return None
        year = int(m.group(3)) if m.group(3) else base.year
        target = base.replace(year=year, month=_MONTHS[m.group(1)], day=int(m.group(2)),
                              hour=hour, minute=minute, second=0, microsecond=0)
        if not m.group(3) and target.timestamp() < now - 86400:
            target = target.replace(year=year + 1)
    else:
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() < now:
            target += timedelta(days=1)
    return int(target.timestamp())


def to_rate_limits(blocks: List[Block]) -> Dict[str, Dict]:
    """The same shape the status line reports, so one reader handles both."""
    out: Dict[str, Dict] = {}
    for b in blocks:
        entry: Dict = {"used_percentage": b.used, "resets_at": b.resets_at,
                       "resets_text": b.resets_text, "title": b.title}
        if b.detail:
            entry["detail"] = b.detail
        out[b.key] = entry
    return out
