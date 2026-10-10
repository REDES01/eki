"""A skills card's skills over HTTP: add one from a folder or git URL, remove one: routes for apiroutes.

The card folder holds one sub-folder per skill (eki/components/skillcard.py does the work);
the card is found in the project's own components/ like every other card edit, so a project
with no folder, an unknown card or a card of another kind is refused before anything is written.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards
from .components import skillcard


def _card(conn: sqlite3.Connection, pid: str, name: str) -> Path:
    """The folder of the project's skills card `name`; KeyError/ValueError when it isn't one."""
    api_cards.writable_root(conn, pid)
    card = api_cards.card_of(conn, pid, name)
    if card["ref"]:
        raise ValueError("a pool component: add skills to it in the pool")
    if card["kind"] != "skills":
        raise ValueError(f"{name} is a {card['kind'] or 'broken'} card, not a skills card")
    return Path(card["folder"])


def add_skill(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source: a skill folder or git URL is needed")
    added = skillcard.add(_card(conn, pid, name), source, replace=bool(body.get("replace")))
    return {"added": added, "card": api_cards.card_of(conn, pid, name)}


def remove_skill(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name, skill = m.group(1), m.group(2), m.group(3)
    if not skillcard.remove(_card(conn, pid, name), skill):
        raise KeyError(f"card {name} has no skill {skill}")
    return {"removed": skill}


ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/skills", add_skill),
    ("DELETE", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/skills/([\w.-]+)", remove_skill),
]
