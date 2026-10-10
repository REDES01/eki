"""The Settings page's model picture: each provider's read list, and the roles.

GET /api/models is read(); POST /api/models/refresh {"name"} reads one
provider's list again (eki/modellist.py). Each list is the program's own,
floating names (aliases that follow the newest) first, then pinned ids, each
in the program's order. Nothing here writes a settings file: the page saves
a picked target or role through /api/settings/routing like any other edit.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

from . import modellist, providers, roles, targets


def _provider(conn: sqlite3.Connection, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(cfg.get("kind") or "")
    listed = modellist.listed(conn, name)
    if listed is not None:
        listed = [m for m in listed if m["floating"]] + [m for m in listed if not m["floating"]]
    return {"kind": kind, "listed": listed,
            "read_at": modellist.read_at(conn, name), "lists": kind in modellist.KINDS}


def _role(conn: sqlite3.Connection, role: str) -> Dict[str, Any]:
    p = roles.pick(role, conn)
    effort = getattr(p, "effort", None)
    shown = targets.label(p.provider, p.model, effort, modellist.listed(conn, p.provider)) if p.provider else ""
    return {"target": roles.get(role), "provider": p.provider, "model": p.model, "effort": effort,
            "shown": shown, "why": p.why, "default": roles.default(role),
            "set": roles.get(role) != roles.default(role)}


def read(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {"providers": {n: _provider(conn, n, c) for n, c in providers.config().items()},
            "roles": {r: _role(conn, r) for r in roles.ROLES}}


def refresh(conn: sqlite3.Connection, name: str) -> Dict[str, Any]:
    """Read one provider's list again; a program that can't say gives a 400."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("say which provider: {\"name\": ...}")
    try:
        modellist.refresh(conn, name)
    except (OSError, RuntimeError, TimeoutError) as e:
        raise ValueError(f"{name}: can't read its model list: {e}") from e
    return read(conn)
