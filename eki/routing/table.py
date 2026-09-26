"""The routing table: rows of kinds of request, each with where it goes.

A plain JSON file in EKI_HOME (`routing.json`) you can read and edit.
Targets are provider names, first choice first; moving down a row is
failover. Each row says what it `needs` from a provider (see needs.py).
The `general` row is used when the prompt check can't run; its targets
go cheapest first: local models, then subscriptions, then the rest.

Migration: an existing routing.json is never rewritten or reordered.
The defaults here (needs, the cheapest-first general row) only reach a
file eki writes for the first time; what a person wrote stays as written.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import paths, providers

#: cheapest first: a local model, then a subscription, then anything else
KIND_ORDER: Dict[str, int] = {"local": 0, "claude_code": 1, "codex": 1}


def cheapest_first(targets: List[str], kinds: Optional[Dict[str, str]] = None) -> List[str]:
    """`targets` in kind order, stable within a kind. `kinds` maps a name
    to its kind; by default the kinds in providers.json."""
    if kinds is None:
        kinds = {n: str(c.get("kind", "")) for n, c in providers.config().items()}
    return sorted(targets, key=lambda t: KIND_ORDER.get(kinds.get(t, ""), 2))


DEFAULT_ROWS: List[Dict[str, Any]] = [
    {"key": "answer", "title": "Answer, explain, write or translate — no tools needed",
     "examples": ["write a haiku about snow", "explain Python's GIL", "translate this to Japanese",
                  "make it about rain instead"],
     "needs": ["text"], "targets": ["local", "claude", "codex"]},
    {"key": "code", "title": "Change code or files, run commands, build or fix something",
     "examples": ["fix the failing test", "add a --json flag to the CLI", "build a small RPG in pygame",
                  "what's taking space in my Downloads folder"],
     "needs": ["tools"], "targets": ["claude", "codex"]},
    {"key": "web", "title": "Needs current information from the web",
     "examples": ["latest Claude Code release notes", "price of an M5 Mac mini today"],
     "needs": ["web"], "targets": ["claude", "codex"]},
    {"key": "general", "title": "Anything (used when the prompt check can't run)",
     "examples": [],
     "needs": [], "targets": cheapest_first(
         ["claude", "codex", "local"],
         {n: str(c["kind"]) for n, c in providers.DEFAULTS.items()})},
]


def settings() -> Dict[str, Any]:
    """The whole routing.json, written with the defaults if it's missing."""
    path = paths.config("routing")
    if not path.exists():
        path.write_text(json.dumps({"rows": DEFAULT_ROWS}, indent=2, ensure_ascii=False) + "\n")
    return dict(json.loads(path.read_text()))


def checker_wakes() -> bool:
    """Whether a checker that is off (an on-demand model) is started for the check."""
    return bool(settings().get("checker_wakes", True))


def rows() -> List[Dict[str, Any]]:
    return list(settings().get("rows") or DEFAULT_ROWS)


def row(key: str) -> Dict[str, Any]:
    for r in rows():
        if r["key"] == key:
            return r
    for r in rows():
        if r["key"] == "general":
            return r
    return DEFAULT_ROWS[-1]
