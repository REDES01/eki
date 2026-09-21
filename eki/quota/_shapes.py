"""Tolerant readers for undocumented payloads.

These endpoints are not contracts — field names differ between CLI versions
(`utilization` vs `used_percent` vs `usedPercent`; snake vs camel; nested vs
flat). Rather than pin one spelling, look for any of them and fail loudly only
when nothing plausible is present.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


def dig(obj: Any, *names: str) -> Any:
    """First present key among `names`, searched case/style-insensitively."""
    if not isinstance(obj, dict):
        return None
    norm = {k.lower().replace("_", ""): v for k, v in obj.items()}
    for n in names:
        k = n.lower().replace("_", "")
        if k in norm and norm[k] is not None:
            return norm[k]
    return None


def find_window(payload: Any, *aliases: str) -> Optional[Dict[str, Any]]:
    """Locate a window object by any of its names, at any depth."""
    want = {a.lower().replace("_", "") for a in aliases}
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower().replace("_", "") in want and isinstance(v, dict):
                    return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(x for x in cur if isinstance(x, (dict, list)))
    return None


def as_fraction(value: Any, scale: str = "auto") -> Optional[float]:
    """Normalise a utilisation number to 0..1.

    `scale` must be stated by the caller wherever the source is known, because
    guessing is not safe: an API that reports percentages sends `1.0` for one
    percent, and the guess "anything <= 1 is already a fraction" turns that
    into 100% — which is exactly what a bar shows the instant a window resets.

    "percent"  — the value is 0..100
    "fraction" — the value is 0..1
    "auto"     — legacy guess, for payloads whose units we genuinely don't know
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    if scale == "percent":
        return v / 100.0
    if scale == "fraction":
        return v
    return v / 100.0 if v > 1.0 else v


def utilization_of(win: Dict[str, Any], scale: str = "auto") -> Optional[float]:
    v = dig(win, "utilization", "used_percent", "usedPercent", "percent_used",
            "percentUsed", "usage_percent", "percent")
    frac = as_fraction(v, scale)
    if frac is not None:
        return frac
    used, limit = dig(win, "used", "used_tokens"), dig(win, "limit", "limit_tokens")
    try:
        if used is not None and limit:
            return float(used) / float(limit)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def epoch_of(value: Any) -> Optional[int]:
    """Accept unix seconds, unix millis, or an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:            # millis
            v /= 1000.0
        return int(v) if v > 1e8 else None
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return epoch_of(int(s))
        try:
            return int(
                datetime.fromisoformat(s.replace("Z", "+00:00"))
                .astimezone(timezone.utc).timestamp()
            )
        except ValueError:
            return None
    return None


def resets_of(win: Dict[str, Any], now: int) -> Optional[int]:
    ts = epoch_of(dig(win, "resets_at", "resetsAt", "reset_at", "resetTime",
                      "reset_time", "next_reset"))
    if ts:
        return ts
    for key in ("resets_in_seconds", "resetsInSeconds", "seconds_until_reset",
                "resets_in", "resetsIn"):
        v = dig(win, key)
        if v is not None:
            try:
                return now + int(float(v))
            except (TypeError, ValueError):
                pass
    mins = dig(win, "resets_in_minutes", "resetsInMinutes", "minutes_until_reset")
    if mins is not None:
        try:
            return now + int(float(mins) * 60)
        except (TypeError, ValueError):
            pass
    return None


def window_seconds_of(win: Dict[str, Any]) -> Optional[int]:
    mins = dig(win, "window_minutes", "windowMinutes", "window_duration_mins",
               "windowDurationMins", "window_size_minutes")
    if mins is not None:
        try:
            return int(float(mins) * 60)
        except (TypeError, ValueError):
            pass
    secs = dig(win, "window_seconds", "windowSeconds", "window")
    try:
        return int(float(secs)) if secs is not None else None
    except (TypeError, ValueError):
        return None


def percent_map(obj: Any, scale: str = "auto") -> Dict[str, float]:
    """Per-model / per-bucket breakdowns, as {name: fraction}."""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            frac = (as_fraction(v, scale) if not isinstance(v, dict)
                    else utilization_of(v, scale))
            if frac is not None:
                out[str(k)] = frac
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                name = dig(item, "model", "name", "key")
                frac = utilization_of(item, scale)
                if name and frac is not None:
                    out[str(name)] = frac
    return out


def assign_slots(found: dict) -> dict:
    """Put parsed windows in the short/long slots by how long they actually are.

    Providers disagree about names: "primary" is a 5-hour window on one plan and
    a 30-day one on another. Duration is the honest discriminator, and a source
    that reports only one window gets one meter rather than a fabricated pair.
    """
    items = [(k, w) for k, w in found.items() if w is not None]
    if not items:
        return {}
    if len(items) == 1:
        w = items[0][1]
        secs = w.window_seconds or 0
        return {"long" if secs >= 2 * 86400 else "short": w}
    items.sort(key=lambda kv: kv[1].window_seconds or 0)
    return {"short": items[0][1], "long": items[-1][1]}
