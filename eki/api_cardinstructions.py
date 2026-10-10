"""An instructions card's file: edit it, link CLAUDE.md to it, read a file it points at: routes for apiroutes.

The card is the project's own AGENTS.md (its `"file"`): eki writes only that file and CLAUDE.md, and
never commits. Reading serves only files inside the project folder (instructions.safe_path).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards
from .components import instructions


def _card(conn: sqlite3.Connection, pid: str, name: str) -> Tuple[Any, Dict[str, Any]]:
    """The project root and its instructions card `name`; KeyError/ValueError when it has none."""
    root = api_cards.writable_root(conn, pid)
    card = api_cards.card_of(conn, pid, name)
    if card["ref"]:
        raise ValueError("a pool component: attach it to a project's own card to edit the file")
    if card["kind"] != "instructions" or card["error"]:
        raise ValueError(f"{name} is not an instructions card")
    return root, card


def _file(card: Dict[str, Any]) -> str:
    return str((card["spec"] or {}).get("file", "AGENTS.md"))


def save(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root, card = _card(conn, pid, name)
    if not isinstance(body.get("text"), str):
        raise ValueError('send {"text": the file\'s new text}')
    instructions.save(root, _file(card), body["text"])
    return {"card": api_cards.card_of(conn, pid, name)}


def link_claude(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root, card = _card(conn, pid, name)
    changed = instructions.link_claude(root, _file(card))
    return {"card": api_cards.card_of(conn, pid, name), "changed": changed}


def read(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root, _ = _card(conn, pid, name)
    rel = q.get("path")
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("send ?path=<a file inside the project>")
    p = instructions.safe_path(root, rel)
    if not p.is_file():
        raise KeyError(f"no file {rel} in the project")
    try:
        return {"path": rel, "text": p.read_text(errors="replace")}
    except OSError as e:
        raise KeyError(f"can't read {rel}: {e}")


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/instructions", save),
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/link-claude", link_claude),
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/read", read),
]
