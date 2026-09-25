"""The routing table: rows of kinds of request, each with where it goes.

A plain JSON file in EKI_HOME (`routing.json`) you can read and edit.
Targets are provider names, first choice first; moving down a row is
failover. The `general` row is used when the prompt check can't run.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .. import paths

DEFAULT_ROWS: List[Dict[str, Any]] = [
    {"key": "answer", "title": "Answer, explain, write or translate — no tools needed",
     "examples": ["write a haiku about snow", "explain Python's GIL", "translate this to Japanese",
                  "make it about rain instead"],
     "targets": ["local", "claude", "codex"]},
    {"key": "code", "title": "Change code or files, run commands, build or fix something",
     "examples": ["fix the failing test", "add a --json flag to the CLI", "build a small RPG in pygame",
                  "what's taking space in my Downloads folder"],
     "targets": ["claude", "codex"]},
    {"key": "web", "title": "Needs current information from the web",
     "examples": ["latest Claude Code release notes", "price of an M5 Mac mini today"],
     "targets": ["claude", "codex"]},
    {"key": "general", "title": "Anything (used when the prompt check can't run)",
     "examples": [],
     "targets": ["claude", "codex", "local"]},
]


def rows() -> List[Dict[str, Any]]:
    path = paths.config("routing")
    if not path.exists():
        path.write_text(json.dumps({"rows": DEFAULT_ROWS}, indent=2, ensure_ascii=False) + "\n")
    data = json.loads(path.read_text())
    return list(data.get("rows") or DEFAULT_ROWS)


def row(key: str) -> Dict[str, Any]:
    for r in rows():
        if r["key"] == key:
            return r
    for r in rows():
        if r["key"] == "general":
            return r
    return DEFAULT_ROWS[-1]
