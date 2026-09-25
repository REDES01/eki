"""Can a provider take work right now?

Installed (or, for a local model, running), not switched off, and not
cooling down after a limit. A limit is learned from the program itself —
it said so, with a reset time — never from reading its credentials.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Dict, Tuple

from . import providers, store

#: a limit that came without a reset time is waited out this long
DEFAULT_COOLDOWN = 15 * 60


def status(conn: sqlite3.Connection) -> Dict[str, Tuple[bool, str]]:
    cooling = store.cooling(conn)
    out: Dict[str, Tuple[bool, str]] = {}
    for name, cfg in providers.config().items():
        if cfg.get("off"):
            out[name] = (False, "switched off")
            continue
        if name in cooling:
            until = cooling[name]["until"]
            out[name] = (False, f"{cooling[name]['reason']} until {time.strftime('%H:%M', time.localtime(until))}")
            continue
        try:
            out[name] = providers.build(name, cfg).available()
        except KeyError as e:
            out[name] = (False, str(e))
    return out


def limited(conn: sqlite3.Connection, provider: str, reset_at) -> float:
    until = float(reset_at) if reset_at else time.time() + DEFAULT_COOLDOWN
    store.cool(conn, provider, until, "limit reached")
    return until
