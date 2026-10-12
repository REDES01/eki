"""Sensitive picture marks over HTTP: its routes for `apiroutes`."""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import artifacts, sensitive


def listing(conn: sqlite3.Connection, m: "re.Match[str]", q: Dict[str, str], body: dict) -> Dict[str, Any]:
    """GET /api/sensitive"""
    return {"paths": sorted(sensitive.paths(conn))}


def set_mark(conn: sqlite3.Connection, m: "re.Match[str]", q: Dict[str, str], body: dict) -> Dict[str, Any]:
    """POST /api/sensitive {"path", "sensitive"} — always the person's own mark."""
    raw, on = body.get("path"), body.get("sensitive")
    if not isinstance(raw, str) or not raw or not isinstance(on, bool):
        raise ValueError("send a path and sensitive: true or false")
    path = sensitive.norm(raw)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in artifacts.KINDS["picture"] or not os.path.isfile(path):
        raise ValueError(f"{raw} is not a picture on disk")
    sensitive.mark(conn, path, on, by="person")
    return {"path": path, "sensitive": on}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/sensitive", listing),
    ("POST", r"/api/sensitive", set_mark),
]
