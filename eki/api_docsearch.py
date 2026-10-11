"""GET /api/doc/search?q=&project=: markdown to open in the side panel (eki/docsearch.py)."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import docsearch


def search(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"files": docsearch.search(conn, q.get("q") or "", q.get("project") or None)}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/doc/search", search),
]
