"""Editing a card: its settings and an agent's prompt, validated by its kind: routes for apiroutes.

Settings are merged into the card's component.json and checked with the same checks
components.load runs, before anything is written: an invalid change writes nothing and
answers each problem beside its field. A pool reference card is read-only here (edit the
pool component in Settings); a project with no folder can't be changed at all.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from . import api_cards, components
from .api_settings import Invalid

MASKED = "••••"
POOL = "a pool component: edit it in the pool"
FIELD = re.compile(r"^([a-z_][a-z0-9_]*):?\b")


def _editable(conn: sqlite3.Connection, pid: str, name: str) -> Dict[str, Any]:
    """The card `name`, when it can be changed; the reason as a ValueError/KeyError when not."""
    api_cards.writable_root(conn, pid)
    card = api_cards.card_of(conn, pid, name)
    if card["ref"]:
        raise ValueError(POOL)
    return card


def _field(msg: str, keys: set) -> str:
    """The field an error is about: its first word when that names a setting, else ""."""
    if "prompt.md" in msg:
        return "prompt"
    m = FIELD.match(msg)
    return m.group(1) if m and m.group(1) in keys else ""


def problems(spec: dict, folder: Path) -> Dict[str, str]:
    """What components.load would object to in `spec`, keyed by field; {} when it's fine."""
    errs = components._check_common(spec)
    if not errs:
        try:
            errs = list(components.kind_module(spec["kind"]).validate(spec, folder) or [])
        except Exception as e:  # a kind's validate must never break the page
            errs = [f"validate failed: {type(e).__name__}: {e}"]
    keys = set(spec) | {"kind", "about", "access", "slots", "memory_gb", "conflicts", "idle_stop"}
    out: Dict[str, str] = {}
    for e in errs:
        k = _field(e, keys)
        out[k] = f"{out[k]}; {e}" if k in out else e
    return out


def _write(f: Path, text: str) -> None:
    tmp = f.with_name(f".{f.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, f)


def _keep_secrets(spec: dict, settings: dict) -> dict:
    """An mcp card's server.env value sent back masked (as the card shows it) keeps the stored one."""
    server = settings.get("server")
    if spec.get("kind") != "mcp" or not isinstance(server, dict) or not isinstance(server.get("env"), dict):
        return settings
    old = (spec.get("server") or {}).get("env") if isinstance(spec.get("server"), dict) else None
    old = old if isinstance(old, dict) else {}
    env = {k: old[k] if isinstance(v, str) and v.startswith(MASKED) and k in old else v
           for k, v in server["env"].items()}
    return {**settings, "server": {**server, "env": env}}


def save_settings(folder: Path, settings: Any) -> None:
    """Merge `settings` into folder/component.json (None removes a key); Invalid writes nothing."""
    if not isinstance(settings, dict) or not settings:
        raise ValueError("settings must be an object of setting: value")
    f = folder / "component.json"
    try:
        spec = json.loads(f.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"component.json unreadable, fix it by hand first: {e}")
    if not isinstance(spec, dict):
        raise ValueError("component.json must hold an object: fix it by hand first")
    if "kind" in settings and settings["kind"] != spec.get("kind"):
        bad = {"kind": "kind can't change: add a new card of the other kind"}
        e = Invalid(list(bad.values()))
        e.problems = bad  # type: ignore[assignment]
        raise e
    merged = dict(spec)
    for k, v in _keep_secrets(spec, settings).items():
        if not isinstance(k, str) or not k:
            raise ValueError(f"bad setting name {k!r}")
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    bad = problems(merged, folder)
    if bad:
        e = Invalid(list(bad.values()))
        e.problems = bad  # type: ignore[assignment]
        raise e
    _write(f, json.dumps(merged, indent=2, ensure_ascii=False) + "\n")


def save_prompt(card: Dict[str, Any], text: Any) -> None:
    if card["kind"] != "agent":
        raise ValueError(f"{card['name']} is a {card['kind'] or 'broken'} component: only an agent has a prompt")
    if not isinstance(text, str):
        raise ValueError("prompt must be text")
    _write(Path(card["folder"]) / "prompt.md", text)


def edit(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    pid, name = m.group(1), m.group(2)
    card = _editable(conn, pid, name)
    if "settings" in body:
        save_settings(Path(card["folder"]), body["settings"])
    elif "prompt" in body:
        save_prompt(card, body["prompt"])
    else:
        raise ValueError('send {"settings": {...}} or {"prompt": text}')
    return {"card": api_cards.card_of(conn, pid, name)}


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([\w.-]+)/cards/([\w.-]+)", edit),
]
