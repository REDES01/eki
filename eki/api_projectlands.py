"""Where a project's work lands, for the window: gh's status, and a project's lands read and set.

GET /api/github?folder=<path> is what the New project dialog shows (signed in, the person's
repositories, a folder's GitHub origin); /api/projects/<id>/lands reads and sets the choice on
the project's workflow card (eki/projectlands.py). eki itself has no choice here.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, List, Tuple

from . import projectchats, projectlands


def _pid(conn: sqlite3.Connection, key: str) -> str:
    pid = projectchats.find(conn, key)
    if pid == projectchats.SELF:
        raise ValueError("eki's own work lands through its own train: there is nothing to choose")
    return pid


def github(conn, m, q, body) -> dict:
    return projectlands.status(q.get("folder") or None)


def get(conn, m, q, body) -> dict:
    return projectlands.lands_of(conn, _pid(conn, m.group(1)))


def post(conn, m, q, body) -> dict:
    pid = _pid(conn, m.group(1))
    return projectlands.set_lands(conn, pid, str(body.get("lands") or ""), repo=body.get("repo") or None,
                                  create=bool(body.get("create")), private=body.get("private", True) is not False)


ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/github", github),
    ("GET", r"/api/projects/([\w.-]+)/lands", get),
    ("POST", r"/api/projects/([\w.-]+)/lands", post),
]
