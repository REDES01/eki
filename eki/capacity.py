"""Can a provider take work right now?

Installed (or, for a local model, running), not switched off, not cooling
down after a limit — and, for a subscription, not near the end of its
plan. The plan is read from the program itself (eki/quota.py), never from
its credentials. In `routing.json` under "quota":

    stop_at            a window this full is treated as used up (0.98)
    save_from          past this, a subscription moves to the end of its row (0.8)

Background work also asks eki/budget.py about every window of a plan: a
share by day and a larger one at night (never the last 10%), and for the
week and the month, no faster than the window goes by. Work you're waiting
for ignores the budget; a run already going is never stopped.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple

from . import budget, models, paths, providers, quota, store

#: a limit that came without a reset time is waited out this long
DEFAULT_COOLDOWN = 15 * 60
STARTS = "off; starts for the run"          # a managed server that is down but eki can start
DEFAULT_LIMITS = {"stop_at": 0.98, "save_from": 0.8}


def limits() -> Dict[str, Any]:
    """stop_at and save_from as numbers, plus the budget's settings."""
    try:
        got = json.loads(paths.config("routing").read_text()).get("quota") or {}
    except (OSError, ValueError):
        got = {}
    return {**{k: float(got.get(k, v)) for k, v in DEFAULT_LIMITS.items()}, **budget.settings()}


def _kept(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Why background work must leave this subscription alone, if it must."""
    r = quota.reading(conn, name)
    t = time.time()
    for window, w in ((r or {}).get("windows") or {}).items():
        ok, why = budget.allows(window, float(w.get("used") or 0), w.get("resets_at"), t)
        if not ok:
            return why
    return None


def _clock(t: Optional[float]) -> str:
    return time.strftime("%a %H:%M", time.localtime(t)) if t else "later"


def status(conn: sqlite3.Connection, background: bool = False) -> Dict[str, Tuple[bool, str]]:
    cooling = store.cooling(conn)
    lim = limits()
    out: Dict[str, Tuple[bool, str]] = {}
    for name, cfg in providers.config().items():
        if cfg.get("off"):
            out[name] = (False, "switched off")
            continue
        if name in cooling:
            out[name] = (False, f"{cooling[name]['reason']} until {_clock(cooling[name]['until'])}")
            continue
        full = quota.fullest(conn, name)
        if full and full["used"] >= lim["stop_at"]:
            out[name] = (False, f"{full['label']} window used up until {_clock(full.get('resets_at'))}")
            continue
        kept = _kept(conn, name) if background else None
        if kept:
            out[name] = (False, "kept for you: " + kept)
            continue
        try:
            out[name] = providers.build(name, cfg).available()
        except KeyError as e:
            out[name] = (False, str(e))
        if not out[name][0] and cfg.get("kind") in models.MANAGED_KINDS and cfg.get("serve"):
            out[name] = (True, STARTS)     # on demand: the worker starts it and waits
    return out


def saving(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Why a subscription should be used last, if it should."""
    full = quota.fullest(conn, name)
    if full and full["used"] >= limits()["save_from"]:
        return f"{full['label']} {round(full['used'] * 100)}%"
    return None


def limited(conn: sqlite3.Connection, provider: str, reset_at) -> float:
    until = float(reset_at) if reset_at else time.time() + DEFAULT_COOLDOWN
    store.cool(conn, provider, until, "limit reached")
    return until
