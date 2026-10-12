"""GET /api/goals/<id>/checks: a goal's checks, each with every verdict filed for it, newest first.

What the project page opens when you click a check line (eki/web/projectchecks.js). Read from the
checks and verdicts tables on every call (eki/verdicts.py), so it holds nothing.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import verdicts

#: every column of a verdict row, as the record shows it
FIELDS = ("id", "run_id", "goal_id", "item_id", "check_id", "scope", "round", "verdict", "evidence", "reasoning",
          "counter_evidence", "limitation", "suggestion", "provider", "model", "created_at")


def checks(conn: sqlite3.Connection, gid: str) -> Dict[str, Any]:
    """{goal, checks: [{id, text, verdict, scope, round, provider, model, at, records: [...]}]};
    KeyError for an unknown goal."""
    if conn.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone() is None:
        raise KeyError(f"no goal {gid}")
    out = []
    for c in verdicts.view(conn, gid):
        c["records"] = [{k: r[k] for k in FIELDS} for r in verdicts.history(conn, gid, c["id"])]
        out.append(c)
    return {"goal": gid, "checks": out}


def get(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    return checks(conn, m.group(1))


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/goals/([\w.-]+)/checks", get),
]
