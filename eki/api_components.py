"""Settings' Components section over HTTP, read-only: its routes for `apiroutes`.

The pool, then each project with a folder: every component as `components.listing` has it,
plus its component.json as `spec` (None for a broken one whose file can't be read).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import components, projectchats


def _spec(entry: dict) -> Optional[dict]:
    try:
        got = json.loads((Path(entry["folder"]) / "component.json").read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _with_spec(rows: List[dict]) -> List[dict]:
    return [{**r, "spec": _spec(r)} for r in rows]


def component_list(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pool = _with_spec(r for r in components.listing(None, conn) if r["scope"] == "pool")
    projects = []
    for p in projectchats.listing(conn):
        if not p.get("folder") or not p.get("folder_ok"):
            continue
        own = [r for r in components.listing(p["folder"], conn) if r["scope"] == "project"]
        projects.append({"project": p["name"], "folder": p["folder"], "components": _with_spec(own)})
    return {"pool": pool, "projects": projects}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/components", component_list),
]
