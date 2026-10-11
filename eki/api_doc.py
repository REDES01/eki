"""A local text file in the side panel: read, saved over the text it was loaded from, and the
`eki open` queue the window polls. The rules for what may be read or written are `docs.policy`."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import docs


def read(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return docs.read(conn, q.get("path") or "")


def write(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return docs.write(conn, body.get("path") or "", body.get("text"), body.get("base_hash") or "")


def request_open(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"id": docs.request_open(conn, body.get("path") or "")}


def opens(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    after = q.get("after")
    try:
        return docs.opens(conn, int(after) if after not in (None, "") else None)
    except (TypeError, ValueError):
        raise ValueError("after must be a number") from None


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/doc", read),
    ("POST", r"/api/doc", write),
    ("POST", r"/api/doc/open", request_open),
    ("GET", r"/api/doc/opens", opens),
]
