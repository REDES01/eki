"""A knowledge card on the project page: its files (name, size, last change, who changed it)
and one file opened to read and edit. Routes come with the card's web part (cardknowledge.js).

Only a path the card's `files()` lists can be read or written, and only when it resolves inside
the project root (a symlink pointing out is refused). Writing needs a project with a folder and a
card of the project's own (a pool reference card is read-only here, as in api_cardedit). A file
that isn't UTF-8 text is an error saying so. Writes go to a temp file then replace the old one.
"""
from __future__ import annotations

import glob
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards, components
from .api_cardedit import POOL
from .components import knowledge


def _comp(conn: sqlite3.Connection, pid: str, name: str) -> components.Component:
    """The project's knowledge card `name` as a Component; KeyError/ValueError when it isn't one."""
    card = api_cards.card_of(conn, pid, name)
    if card["kind"] != "knowledge":
        raise ValueError(f"{name} is a {card['kind'] or 'broken'} card, not a knowledge card")
    root = api_cards.project_root_of(conn, pid)
    found, _ = components.own(root)
    if name not in found:
        raise KeyError(f"project {pid} has no card {name}")
    return found[name]


def _entry(comp: components.Component, rel: Any) -> Tuple[Dict[str, Any], Path]:
    """The card's entry for `rel` and the file it resolves to inside the root; ValueError when
    the card doesn't list it or it points outside the project."""
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("path: one of the card's files is needed")
    rel = os.path.normpath(rel.strip())
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"{rel} must be a path inside the project, with no ..")
    for e in knowledge.files(comp):
        if os.path.normpath(e["path"]) != rel:
            continue
        if e["missing"] and glob.has_magic(e["pattern"]):
            raise ValueError(f"{rel} matches no file")
        root = knowledge.root_of(comp).resolve()
        try:
            real = (root / rel).resolve()
        except OSError as x:
            raise ValueError(f"{rel}: {x}")
        if real != root and root not in real.parents:
            raise ValueError(f"{rel} points outside the project")
        return e, real
    raise ValueError(f"{rel} is not one of {comp.name}'s files")


def files(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    return {"files": knowledge.files(_comp(conn, m.group(1), m.group(2)))}


def read(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    e, real = _entry(_comp(conn, m.group(1), m.group(2)), q.get("path"))
    if not real.is_file():
        raise ValueError(f"{e['path']} is missing in the project folder")
    try:
        text = real.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{e['path']} is not UTF-8 text: open it outside eki")
    except OSError as x:
        raise ValueError(f"{e['path']}: {x}")
    return {"path": e["path"], "text": text, "size": e["size"], "changed": e["changed"], "author": e["author"]}


def write(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    api_cards.writable_root(conn, pid)
    if api_cards.card_of(conn, pid, name)["ref"]:
        raise ValueError(POOL)
    comp = _comp(conn, pid, name)
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be text")
    e, real = _entry(comp, body.get("path"))
    if real.is_dir():
        raise ValueError(f"{e['path']} is a folder")
    if real.is_file():
        try:
            real.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{e['path']} is not UTF-8 text: edit it outside eki")
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_name(f".{real.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, real)
    for got in knowledge.files(comp):
        if os.path.normpath(got["path"]) == os.path.normpath(e["path"]):
            return got
    return e


#: (method, regex, fn) — see eki/apiroutes.py; the card-edit route's `[\w.-]+` never matches a "/",
#: so these are not shadowed by it
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/files", files),
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/file", read),
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/file", write),
]
