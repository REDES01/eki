"""Running a card through the engine and listing its uses: routes for apiroutes.

A Run from the panel is exactly the run `eki use <name> <args>` makes from the project's
root (eki.cli.use.create): provider 'component', priority 'now', no caller run, so the
engine queues it, the access rules hold it and runs.why says why it waits. Uses are read
back from the runs table (eki.components.uses); nothing new is recorded.
"""
from __future__ import annotations

import json
import shlex
import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards, store
from .components import uses


def _card(conn: sqlite3.Connection, pid: str, name: str) -> Dict[str, Any]:
    """The card, ready to call: KeyError when the project has none, ValueError when broken."""
    c = api_cards.card_of(conn, pid, name)
    if c["error"]:
        raise ValueError(f"card {name} is broken: {c['error']}")
    return c


def run_card(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root = api_cards.writable_root(conn, pid)
    text = body.get("args") or ""
    if not isinstance(text, str):
        raise ValueError('"args" is text')
    try:
        args = shlex.split(text)
    except ValueError as e:
        raise ValueError(f"args: {e}")
    _card(conn, pid, name)
    from .cli import use                     # the server lives in the engine: writing the run is enough
    return {"run": use.create(conn, name, args, caller_dir=str(root), caller_run=None)}


def card_uses(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    root = api_cards.project_root_of(conn, pid)
    return {"uses": uses.calls(conn, name, root, 20) if root is not None else []}


def _paths(conn: sqlite3.Connection, rid: str, name: str) -> List[str]:
    out: List[str] = []
    for ev in store.events_after(conn, rid):
        if ev["kind"] != "tool":
            continue
        data = json.loads(ev["data"] or "{}")
        p = data.get("path")
        if data.get("name") == f"eki use {name}" and p and p not in out:
            out.append(p)
    return out


def card_use(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    """One call, for the panel's live view; KeyError when the run isn't a call of this card."""
    pid, name, rid = m.group(1), m.group(2), m.group(3)
    root = api_cards.project_root_of(conn, pid)
    got = next((c for c in uses.calls(conn, name, root, limit=10 ** 9) if c["run"] == rid), None) \
        if root is not None else None
    if got is None:
        raise KeyError(f"run {rid} is not a call of {name} from project {pid}")
    r = store.run(conn, rid)
    return {**got, "why": r["why"] or "", "text": store.answer(conn, rid), "paths": _paths(conn, rid, name)}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/run", run_card),
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/uses", card_uses),
    ("GET", r"/api/projects/([\w.-]+)/cards/([\w.-]+)/uses/([\w.-]+)", card_use),
]
