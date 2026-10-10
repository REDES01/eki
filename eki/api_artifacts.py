"""The window's Artifacts page over HTTP: its routes for `apiroutes`."""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import artifacts


def listing(conn: sqlite3.Connection, m: "re.Match[str]", q: Dict[str, str], body: dict) -> Dict[str, Any]:
    """GET /api/artifacts?kind=all|picture|page|document&q=&before=&limit="""
    try:
        limit = max(1, min(500, int(q.get("limit") or 60)))
        before = float(q["before"]) if q.get("before") else None
    except ValueError:
        raise ValueError("limit must be a whole number and before an event time")
    return artifacts.listing(conn, kind=q.get("kind") or "all", q=q.get("q") or "",
                             limit=limit, before=before)


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/artifacts", listing),
]
