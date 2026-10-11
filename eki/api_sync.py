"""Settings' Claude sync section over HTTP, read-only: its routes for `apiroutes`.

Each direction in `syncstate.DIRECTIONS` order as `syncstate.status()` has it, and the
instructions diff (root()/instructions.diff) when there is one.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import syncstate


def sync_status(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    state = syncstate.status()
    directions = [{"name": d, "at": state[d].get("at"), "count": int(state[d].get("count") or 0),
                   "clashes": [str(c) for c in state[d].get("clashes") or []], "note": str(state[d].get("note") or "")}
                  for d in syncstate.DIRECTIONS]
    try:
        diff = (syncstate.root() / "instructions.diff").read_text()
    except OSError:
        diff = ""
    return {"directions": directions, "diff": diff}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/sync", sync_status),
]
