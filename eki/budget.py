"""How much of a subscription background work may spend, by the clock.

Work you are waiting for ignores this; work eki does on its own asks first.
In `routing.json` under "quota":

    background_day     share of each window background work may use by day (0.5)
    background_night   the same at night (0.9)
    night              local hours that count as night ("23:00-07:00")

Both shares are held to at most NEVER_ABOVE, so the last tenth of any window
is always yours. A `background_up_to` written before these existed is read
as `background_day`; the file is never rewritten.

Windows a day or longer are also held to pace: background work may use one
only while it has used no more than the share of the window already gone.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from . import paths, quota

WINDOW_SECONDS = {"five_hour": 18000, "seven_day": 604800, "thirty_day": 2592000}
NEVER_ABOVE = 0.9
DEFAULTS: Dict[str, Any] = {"background_day": 0.5, "background_night": 0.9, "night": "23:00-07:00"}
PACED_FROM = 24 * 3600          # windows at least this long are held to pace


def settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text()).get("quota") or {}
    except (OSError, ValueError):
        got = {}
    day = got.get("background_day", got.get("background_up_to", DEFAULTS["background_day"]))
    night = got.get("night") or DEFAULTS["night"]
    if _span(night) is None:
        night = DEFAULTS["night"]
    return {"background_day": min(float(day), NEVER_ABOVE),
            "background_night": min(float(got.get("background_night", DEFAULTS["background_night"])),
                                    NEVER_ABOVE),
            "night": night}


def _span(text: str) -> Optional[Tuple[int, int]]:
    """"23:00-07:00" → minutes after midnight of its start and end."""
    try:
        a, b = str(text).split("-")
        (ah, am), (bh, bm) = (map(int, a.split(":")), map(int, b.split(":")))
    except ValueError:
        return None
    if not (0 <= ah < 24 and 0 <= bh < 24 and 0 <= am < 60 and 0 <= bm < 60):
        return None
    return ah * 60 + am, bh * 60 + bm


def _is_night(span: Tuple[int, int], now: float) -> bool:
    t = time.localtime(now)
    m, (start, end) = t.tm_hour * 60 + t.tm_min, span
    if start <= end:
        return start <= m < end
    return m >= start or m < end


def ceiling(now: Optional[float] = None) -> Tuple[float, str]:
    s = settings()
    when = "night" if _is_night(_span(s["night"]), time.time() if now is None else now) else "day"
    return s["background_" + when], when


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _other(s: Dict[str, Any], when: str) -> str:
    """The half of the day that isn't now, and when it starts."""
    start, end = s["night"].split("-")
    if when == "day":
        return f"night from {start} up to {_pct(s['background_night'])}"
    return f"day from {end} up to {_pct(s['background_day'])}"


def allows(window: str, used: float, resets_at: Optional[float],
           now: Optional[float] = None) -> Tuple[bool, str]:
    now = time.time() if now is None else now
    s = settings()
    share, when = ceiling(now)
    label = quota.LABELS.get(window, window)
    if used >= share:
        return False, (f"{label} at {_pct(used)} ({when} budget {_pct(share)}; "
                       f"{_other(s, when)})")
    length = WINDOW_SECONDS.get(window)
    if length and length >= PACED_FROM and resets_at:
        gone = min(1.0, max(0.0, 1 - (float(resets_at) - now) / length))
        if used > gone:
            return False, f"{label} ahead of pace ({_pct(used)} used, {_pct(gone)} of it gone)"
    return True, f"{label} at {_pct(used)}, within the {when} budget {_pct(share)}"


def describe(now: Optional[float] = None) -> str:
    s = settings()
    share, when = ceiling(now)
    if when == "day":
        rest = f"night {s['night']} up to {_pct(s['background_night'])}"
    else:
        rest = f"day from {s['night'].split('-')[1]} up to {_pct(s['background_day'])}"
    return f"budget: {when}, background up to {_pct(share)} of each window ({rest})"
