# SPDX-License-Identifier: Apache-2.0
"""Is a window being spent faster than it refills?

A 5-hour window at 90% with four hours to go is not the same as one at 90%
with ten minutes to go: the first should make its provider dearer now, so
medium work goes to a local model and the last of the window is there for
the hard problem that turns up later; the second is about to refill and
can be spent. Likewise a week that's barely touched by Thursday is cheaper
than its list price — leaving it unspent buys nothing.

So each window gets a pace: how far ahead of (or behind) an even burn it is,
turned into a factor on the provider's cost. Ahead by 70 points is ×3.1;
behind by 30 is ×0.5 (the floor); on pace is ×1. A spent window is not a
pace question — the board's `exhausted` still takes it out entirely.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .base import Reading, Window

#: how much a point of used% ahead of elapsed% costs
STEEP = 3.0
FLOOR, CEIL = 0.5, 4.0
#: windows that don't say how long they are
DEFAULT_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400, "credits": 30 * 86400}


@dataclass
class Pace:
    factor: float = 1.0
    #: which window set it: "5H", "WEEK", "CREDITS"
    window: str = ""
    used: float = 0.0
    elapsed: Optional[float] = None       # None: no reset time known
    #: the window is gone and the work is being paid for from credits
    on_credits: bool = False

    @property
    def ahead(self) -> Optional[float]:
        return None if self.elapsed is None else round(self.used - self.elapsed, 3)

    @property
    def why(self) -> str:
        if self.on_credits:
            return f"{self.window} spent, on credits ×{self.factor:g}"
        if not self.window or abs(self.factor - 1.0) < 0.05:
            return ""
        side = "ahead of" if self.factor > 1 else "behind"
        return f"{self.window} {side} pace ×{self.factor:g}"


@dataclass
class ProviderPace:
    pace: Pace = field(default_factory=Pace)
    #: windows that cover one model, by the token in their key ("fable")
    models: Dict[str, Pace] = field(default_factory=dict)

    @property
    def factor(self) -> float:
        return self.pace.factor

    def model_factor(self, model: str) -> float:
        name = (model or "").lower()
        return max((p.factor for token, p in self.models.items() if token in name), default=1.0)


def elapsed_fraction(w: Window, now: Optional[float] = None) -> Optional[float]:
    if w.resets_at is None:
        return None
    seconds = w.window_seconds or next(
        (s for k, s in DEFAULT_SECONDS.items() if w.key.startswith(k)), None)
    if not seconds:
        return None
    left = w.resets_at - (now or time.time())
    return round(min(1.0, max(0.0, 1.0 - left / seconds)), 3)


def window_pace(w: Window, now: Optional[float] = None) -> Pace:
    elapsed = elapsed_fraction(w, now)
    if elapsed is None:
        # no reset time: only a window that's most of the way gone says anything
        factor = 1.0 + STEEP * max(0.0, w.used - 0.7)
    else:
        factor = 1.0 + STEEP * (w.used - elapsed)
    factor = round(min(CEIL, max(FLOOR, factor)), 2)
    return Pace(factor=factor, window=w.label, used=w.used, elapsed=elapsed)


def credits_left(reading: Reading) -> Optional[float]:
    """0..1 of the credits pool still there, or None when there is no pool."""
    pools = [w for w in reading.windows if w.kind == "credits"]
    if not pools:
        return None
    return max(0.0, 1.0 - max(w.used for w in pools))


def provider_pace(reading: Reading, now: Optional[float] = None,
                  ceiling: float = 0.99) -> ProviderPace:
    """The tightest account-wide window sets the provider's pace; a window
    for one model sets that model's. A window that's spent while credits
    remain doesn't take the provider out — the board's `exhausted` leaves it
    in — but it's as dear as pacing gets: every token is now money."""
    out = ProviderPace()
    credits = credits_left(reading)
    for w in reading.windows:
        if w.kind != "window":
            # credits are money spent only once the windows are gone: shown
            # with a pace of their own, but not what prices the provider now
            continue
        p = window_pace(w, now)
        if w.used >= ceiling and credits:
            p = Pace(factor=CEIL, window=w.label, used=w.used, elapsed=p.elapsed, on_credits=True)
        if w.primary:
            if p.factor > out.pace.factor or not out.pace.window:
                out.pace = p
        else:
            token = w.key.rsplit("_", 1)[-1].lower()
            out.models[token] = p
    return out
