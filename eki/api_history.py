"""A project's history over HTTP: its changes in plain words, and going back. Routes for `apiroutes`."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import builds, history, projectchats


def _folder(conn: sqlite3.Connection, key: str) -> str:
    pid = projectchats.find(conn, key)
    if pid == projectchats.SELF:
        return str(builds.source())
    return projectchats.folder_of(conn, pid) or ""


def changes(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return history.changes(conn, _folder(conn, m.group(1)))


def go_back(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    change = str((body or {}).get("change") or "").strip()
    if not change:
        raise ValueError("say which change to go back to before")
    folder = _folder(conn, m.group(1))
    done = history.go_back(conn, folder, change)
    return {"done": done, "history": history.changes(conn, folder)}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/projects/([^/]+)/history", changes),
    ("POST", r"/api/projects/([^/]+)/goback", go_back),
]
