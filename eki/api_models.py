"""The Settings page's model picture: each provider's read list and tiers, and the roles.

GET /api/models is read(); POST /api/models/refresh {"name"} reads one
provider's list again (eki/modellist.py). Nothing here writes a settings
file: the page saves a picked tier or role through /api/settings/(routing|
providers) like any other edit.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

from . import modellist, providers, roles, targets


def _tiers(cfg: Dict[str, Any]) -> Dict[str, str]:
    """The picked providers.json models, with the old `model` as default."""
    got = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    out = {t: str(got[t]) for t in targets.TIERS if got.get(t)}
    if "default" not in out and cfg.get("model"):
        out["default"] = str(cfg["model"])
    return out


def _provider(conn: sqlite3.Connection, name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(cfg.get("kind") or "")
    listed = modellist.listed(conn, name)
    return {"kind": kind, "tiers": _tiers(cfg), "listed": listed,
            "read_at": modellist.read_at(conn, name), "lists": kind in modellist.KINDS}


def _role(conn: sqlite3.Connection, role: str) -> Dict[str, Any]:
    p = roles.pick(role, conn)
    return {"target": roles.get(role), "provider": p.provider, "model": p.model, "why": p.why,
            "default": roles.default(role), "set": roles.get(role) != roles.default(role)}


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
