"""An mcp card (or a service card offering an MCP server) on the project page: its server with env values masked, and a Test that runs a
quick `initialize` handshake and lists its tools. Routes come with the card's web part (cardmcp.js).

Test runs `mcpprobe.probe` on the card's own server (a pool reference card's too) and answers
what it found; nothing is stored. The server comes unmasked from components.own, only to start
it: the answer never carries an env value. A service card's server is its "mcp" field. `/test` has a path segment the card-edit route
(`/cards/<name>`) can't match, so it is never shadowed.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards, components
from .components import mcpprobe


def server_of(conn: sqlite3.Connection, pid: str, name: str) -> dict:
    """The unmasked server of the project's mcp card `name`, or of its service card's "mcp";
    KeyError/ValueError when it has none."""
    card = api_cards.card_of(conn, pid, name)
    if card["kind"] not in ("mcp", "service"):
        raise ValueError(f"{name} is a {card['kind'] or 'broken'} card, not an mcp card")
    field = "server" if card["kind"] == "mcp" else "mcp"
    root = api_cards.project_root_of(conn, pid)
    comp = components.own(root)[0].get(name) if root is not None else None
    if comp is None:
        raise KeyError(f"project {pid} has no card {name}")
    server = comp.spec.get(field)
    if not isinstance(server, dict):
        raise ValueError(f"{name} has no {'server' if field == 'server' else 'MCP server'} to test")
    return server


def test(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return mcpprobe.probe(server_of(conn, m.group(1), m.group(2)))


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/test", test),
]
