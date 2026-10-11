"""What a card's panel says before any settings, built on the server: `live` (the kind's own live state,
from its module's optional `live(comp, conn, root) -> dict`) and `overview` (Used by: the workflow stages and
recent runs that used it; Right now: its state in words). api_cards.py adds both to each card. Neither raises:
a kind's trouble leaves its part empty."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from . import components
from .components import access


def live(conn: sqlite3.Connection, root: Path, comp: components.Component) -> Dict[str, Any]:
    """The kind's live(comp, conn, root); {} when the kind has none, it raises, or it gives back no dict."""
    try:
        fn = getattr(components.kind_module(comp.kind), "live", None)
        got = fn(comp, conn, root) if callable(fn) else {}
    except Exception:  # a kind's live state must never break the page
        return {}
    return got if isinstance(got, dict) else {}


def _now(conn: sqlite3.Connection, comp: components.Component) -> List[str]:
    """The card's state in words: who holds it, running, or idle."""
    try:
        held = access.in_use(conn, comp.name, comp.spec)
    except Exception:
        held = ""
    if held:
        return [held]
    try:
        up = bool(components.kind_module(comp.kind).is_up(comp))
    except Exception:  # a kind's is_up must never break the page
        up = False
    return ["running" if up else "idle"]


def overview(conn: sqlite3.Connection, root: Path, comp: components.Component, live: Dict[str, Any]) -> Dict[str, Any]:
    """{"used_by": {"stages", "line", "runs"}, "now": [lines]} for the card's panel."""
    return {"used_by": {"stages": [], "line": "", "runs": []}, "now": _now(conn, comp)}


def broken(why: str) -> Dict[str, Any]:
    """The overview of a card that can't be read."""
    return {"used_by": {"stages": [], "line": "", "runs": []}, "now": [f"broken: {why}"]}
