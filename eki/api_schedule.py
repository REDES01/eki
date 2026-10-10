"""The window's Scheduled page over HTTP: its routes for `apiroutes`.

GET /api/schedule lists the scheduled prompts (schedule.py) and the standing
goals (standing.py) not dropped, eki's first, each with its project's name.
The POSTs add a schedule and act on one row of either kind.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import modellist, projectchats, schedule, standing, targets

_STANDING = ("id", "project", "text", "state", "why", "rounds", "last_round_at")


def _names(conn: sqlite3.Connection) -> Dict[str, str]:
    """Every project's name by id; eki's under both its id and NULL (standing goals)."""
    out: Dict[Any, str] = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM projects")}
    out[projectchats.SELF] = out[None] = "eki"
    return out


def _target_label(conn: sqlite3.Connection, target: Any) -> str:
    if not target:
        return "Auto"
    try:
        t = targets.parse(target)
    except ValueError:
        return str(target)
    return targets.label(t.provider, t.model or t.tier, t.effort, modellist.listed(conn, t.provider))


def _order(names: Dict[Any, str], project: Optional[str]) -> Tuple[int, str]:
    eki = project is None or project == projectchats.SELF
    return (0 if eki else 1, (names.get(project) or "").lower())


def page(conn: sqlite3.Connection, m: Any = None, q: Any = None, body: Any = None) -> Dict[str, Any]:
    names = _names(conn)
    rows: List[Dict[str, Any]] = []
    for s in schedule.listing(conn):
        rows.append({"id": s["id"], "text": s["text"], "project": s["project"],
                     "project_name": names.get(s["project"]) if s["project"] else None,
                     "cadence": s["cadence"], "cadence_text": s["cadence_text"], "tz": s["tz"],
                     "target": s["target"], "target_label": _target_label(conn, s["target"]),
                     "state": s["state"], "next_at": s["next_at"], "last_at": s["last_at"],
                     "last_thread": s["last_thread"], "why": s["why"]})
    goals = [dict(r) for r in conn.execute(
        f"SELECT {', '.join(_STANDING)} FROM standing WHERE state!='dropped' ORDER BY created_at")]
    for g in goals:
        g["project_name"] = names.get(g["project"], g["project"])
    goals.sort(key=lambda g: _order(names, g["project"]))     # stable: oldest first in a project
    return {"schedules": rows, "standing": goals, "tz": schedule.local_tz()}


def add(conn: sqlite3.Connection, m: Any, q: Any, body: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(body, dict):
        raise ValueError("send {text, cadence, project?, target?}")
    if body.get("cadence") is None:
        raise ValueError("a scheduled prompt needs a cadence")
    try:
        sid = schedule.add(conn, body.get("text") or "", body["cadence"],
                           project=body.get("project") or None, target=body.get("target") or None)
    except KeyError as e:                       # an unknown project is the sender's mistake
        raise ValueError(str(e.args[0]) if e.args else "no such project") from None
    return {"id": sid}


def act(conn: sqlite3.Connection, m: Any, q: Any, body: Any) -> Dict[str, str]:
    sid, verb = m.group(1), m.group(2)
    if verb == "run":
        return {"id": schedule.find(conn, sid)["id"], "thread": schedule.run_now(conn, sid)}
    fn = {"pause": schedule.pause, "resume": schedule.resume, "drop": schedule.drop}[verb]
    return {"id": fn(conn, sid)}


def act_standing(conn: sqlite3.Connection, m: Any, q: Any, body: Any) -> Dict[str, str]:
    fn = {"pause": standing.pause, "resume": standing.resume, "drop": standing.drop,
          "now": standing.now}[m.group(2)]
    return {"id": fn(conn, m.group(1))}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/schedule", page),
    ("POST", r"/api/schedule", add),
    ("POST", r"/api/schedule/([^/]+)/(pause|resume|drop|run)", act),
    ("POST", r"/api/standing/([^/]+)/(pause|resume|drop|now)", act_standing),
]
