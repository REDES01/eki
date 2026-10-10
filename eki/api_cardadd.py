"""Adding a card to a project (a template or a pool card) and removing one: routes for apiroutes.

Both need the project's folder (api_cards.writable_root): with none, a 400 saying why. A card is
added under a free name, never over one; removing moves it to the Trash. Nothing is committed.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards
from .components import cards


def add_card(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid = m.group(1)
    base = api_cards.writable_root(conn, pid) / "components"
    template, pool = body.get("template"), body.get("pool")
    if bool(template) == bool(pool):
        raise ValueError('send {"template": name} or {"pool": name}')
    if template:
        folder, said = cards.add_template(base, str(template))
    else:
        folder, said = cards.attach(base, str(pool)), []
    return {"card": api_cards.card_of(conn, pid, folder.name), "said": said}


def remove_card(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    base = api_cards.writable_root(conn, pid) / "components"
    return {"removed": name, "trash": str(cards.remove(base, name))}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards", add_card),
    ("DELETE", r"/api/projects/([\w.-]+)/cards/([\w.-]+)", remove_card),
]
