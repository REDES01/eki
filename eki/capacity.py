# SPDX-License-Identifier: Apache-2.0
"""How much work each subscription can still take (docs/routing.md).

A percentage hides size: "50% used" on a $20 plan and on a $200 one look the
same, and one has ten times more left. So eki learns, per subscription and
per window, what a typical request costs — the window's percentage moving
between two readings, divided by the requests that finished on it in
between. No plan table, no question: it adapts to any plan, and to a plan
that changes.

From that: requests left in each window, and requests left per hour until
it resets. The tightest window is the subscription's room. Routing orders
subscriptions by it — the one with the most room takes the work first.
Before eki has seen a few requests, room is unknown and the order falls back
to pace (how fast each window is being spent against how long it lasts).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PATH = Path("~/.eki/capacity.json").expanduser()
#: how much a new sample moves the estimate
ALPHA = 0.3
#: samples before the estimate is used
MIN_SAMPLES = 2
CEILING = 0.99


def load() -> Dict[str, Any]:
    try:
        data = json.loads(PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: Dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(PATH)


def observe(data: Dict[str, Any], provider: str, windows: List[Any], runs: int,
            now: Optional[float] = None) -> Dict[str, Any]:
    """Fold one quota reading in. `runs` is how many requests have ever
    finished on this subscription (a running count); the difference since
    the last reading is what the window's movement is shared across."""
    now = now or time.time()
    prov = data.setdefault(provider, {})
    for w in windows:
        if getattr(w, "kind", "window") != "window":
            continue
        key = w.key
        rec = prov.setdefault(key, {"cost": None, "n": 0})
        last = rec.get("last")
        rec["last"] = {"used": float(w.used), "resets_at": w.resets_at, "runs": int(runs), "at": now}
        if not last or last.get("resets_at") != w.resets_at:
            continue                                # first sight, or the window reset
        d_used = float(w.used) - float(last["used"])
        d_runs = int(runs) - int(last["runs"])
        if d_runs <= 0 or d_used <= 0:
            # one moved without the other — requests whose usage hasn't shown
            # yet, or usage from elsewhere (the terminal): keep the older
            # reading, so the next one that has both is measured from it
            if d_used < 0:
                continue                            # a reading going backwards: take the new one
            rec["last"] = last
            continue
        sample = d_used / d_runs
        rec["cost"] = sample if rec["cost"] is None else (1 - ALPHA) * rec["cost"] + ALPHA * sample
        rec["n"] = int(rec.get("n") or 0) + 1
    return data


def room(data: Dict[str, Any], provider: str, windows: List[Any],
         now: Optional[float] = None) -> Dict[str, Any]:
    """What this subscription can still take: per window, requests left and
    requests left per hour; `per_hour` is the tightest window's."""
    now = now or time.time()
    out: Dict[str, Any] = {"windows": [], "per_hour": None, "left": None}
    per_hours, lefts = [], []
    for w in windows:
        if getattr(w, "kind", "window") != "window" or not getattr(w, "primary", True):
            continue
        rec = (data.get(provider) or {}).get(w.key) or {}
        cost = rec.get("cost") if int(rec.get("n") or 0) >= MIN_SAMPLES else None
        row: Dict[str, Any] = {"window": w.label, "used": w.used, "cost": cost, "samples": int(rec.get("n") or 0)}
        if cost:
            left = max(0.0, CEILING - float(w.used)) / cost
            hours = max(0.25, ((w.resets_at or now) - now) / 3600)
            row.update(left=round(left, 1), per_hour=round(left / hours, 2))
            per_hours.append(left / hours)
            lefts.append(left)
        out["windows"].append(row)
    if per_hours:
        out["per_hour"] = round(min(per_hours), 2)
        out["left"] = round(min(lefts), 1)
    return out


def spare(data: Dict[str, Any], provider: str, windows: List[Any], reserve: float) -> Optional[float]:
    """Requests this subscription can take before the part kept for you:
    below `1 - reserve` of the tightest window. None until eki knows what a
    request costs here — the caller decides what an unknown is worth."""
    lefts = []
    for w in windows:
        if getattr(w, "kind", "window") != "window" or not getattr(w, "primary", True):
            continue
        rec = (data.get(provider) or {}).get(w.key) or {}
        cost = rec.get("cost") if int(rec.get("n") or 0) >= MIN_SAMPLES else None
        if not cost:
            return None
        lefts.append(max(0.0, (1.0 - reserve) - float(w.used)) / cost)
    return round(min(lefts), 1) if lefts else None
