# SPDX-License-Identifier: Apache-2.0
"""What a usage limit looks like, whoever reports it.

Two rules carried over from tokenbar, both learned the hard way:

* Every percentage states its scale. A source that reports percent sends 1.0
  for one percent, and guessing turned that into 100% the moment a weekly
  window reset.
* Every reading states when it was observed. A cached number served as fresh
  once froze the bar on stale data while it claimed to be healthy.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Window:
    key: str                          # "five_hour", "seven_day", "spend", …
    label: str                        # what the meter says: "5H", "WEEK"
    used: float                       # 0..1 of the provider's own limit
    resets_at: Optional[int] = None   # unix seconds
    window_seconds: Optional[int] = None
    kind: str = "window"              # "window" | "credits"
    #: False for a limit that covers one model rather than the whole account —
    #: shown in full, but not what the menu bar's two meters are about
    primary: bool = True
    #: anything the meter can't say by itself: "$113.74 / $120.00"
    detail: str = ""


@dataclass
class Reading:
    provider: str
    windows: List[Window] = field(default_factory=list)
    observed_at: Optional[int] = None   # when the provider said this, not when we read it
    error: str = ""
    note: str = ""

    @property
    def age_seconds(self) -> Optional[int]:
        return None if self.observed_at is None else int(time.time()) - self.observed_at

    def to_json(self) -> Dict[str, Any]:
        out = asdict(self)
        out["age_seconds"] = self.age_seconds
        return out


def label_for(seconds: Optional[int], fallback: str) -> str:
    """Name a window by its real length, so a meter never mislabels a quota."""
    if not seconds:
        return fallback
    days = seconds / 86400
    if 6 <= days <= 8:
        return "WEEK"
    if 27 <= days <= 32:
        # Codex on some plans: one 30-day window. Calling it WEEK once made
        # a month's allowance look four times tighter than it was.
        return "MONTH"
    if days >= 1:
        return f"{round(days)}D"
    return f"{round(seconds / 3600)}H"


class QuotaProvider:
    """One source of usage limits. Subclasses implement `fetch`."""

    #: seconds between real fetches; reads in between reuse the last result
    min_interval: float = 60.0

    def __init__(self, key: str, options: Optional[Dict[str, Any]] = None):
        self.key = key
        self.options = options or {}
        self.min_interval = float(self.options.get("min_interval_seconds",
                                                   self.min_interval))
        self._last: Optional[Reading] = None
        self._fetched: float = 0.0

    async def fetch(self) -> Reading:
        raise NotImplementedError

    async def read(self, force: bool = False) -> Reading:
        if (not force and self._last is not None
                and time.time() - self._fetched < self.min_interval):
            return self._last
        try:
            reading = await self.fetch()
        except Exception as e:                      # noqa: BLE001
            # keep the last good windows, but say what went wrong and how old
            reading = Reading(self.key,
                              windows=self._last.windows if self._last else [],
                              observed_at=self._last.observed_at if self._last else None,
                              error=str(e)[:200])
        self._last, self._fetched = reading, time.time()
        return reading
