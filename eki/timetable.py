# SPDX-License-Identifier: Apache-2.0
"""When something repeats: every so many minutes, or daily at a time on
chosen days — a goal's `when` (eki/goals.py). Local time throughout."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional


def describe(spec: Dict[str, Any]) -> str:
    kind = spec.get("kind")
    if kind == "interval":
        minutes = int(spec.get("minutes", 60))
        if minutes % 1440 == 0:
            return f"every {minutes // 1440} day{'s' if minutes > 1440 else ''}"
        if minutes % 60 == 0:
            return f"every {minutes // 60} hour{'s' if minutes > 60 else ''}"
        return f"every {minutes} min"
    if kind == "daily":
        days = sorted(set(int(d) for d in spec.get("days") or range(7)))
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if days == list(range(7)):
            which = "every day"
        elif days == list(range(5)):
            which = "weekdays"
        elif days == [5, 6]:
            which = "weekends"
        else:
            which = ", ".join(names[d] for d in days)
        return f"{which} at {spec.get('at', '09:00')}"
    return "never"


def next_time(spec: Dict[str, Any], after: float, last: Optional[float] = None) -> Optional[float]:
    """The next firing strictly after `after` (unix seconds), local time."""
    kind = spec.get("kind")
    if kind == "interval":
        minutes = max(1, int(spec.get("minutes", 60)))
        base = last if last is not None else after
        nxt = base + minutes * 60
        while nxt <= after:
            nxt += minutes * 60
        return nxt
    if kind == "daily":
        hh, _, mm = str(spec.get("at", "09:00")).partition(":")
        try:
            hour, minute = int(hh), int(mm or 0)
        except ValueError:
            hour, minute = 9, 0
        days = set(int(d) for d in spec.get("days") or range(7))
        now = datetime.fromtimestamp(after)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(8):
            if candidate.timestamp() > after and candidate.weekday() in days:
                return candidate.timestamp()
            candidate += timedelta(days=1)
        return None
    return None
