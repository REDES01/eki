"""The providers on this Mac, from `providers.json` in EKI_HOME.

The file is written with defaults on first use and is yours to edit:

    {"claude": {"kind": "claude_code", "label": "Claude Code"},
     "codex":  {"kind": "codex", "label": "Codex"},
     "local":  {"kind": "local", "base_url": "http://127.0.0.1:8080"}}
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .. import paths
from .base import Outcome, Provider, Turn
from .claude_code import ClaudeCode
from .codex import Codex
from .command import Command
from .fake import Fake
from .local import Local

KINDS = {cls.kind: cls for cls in (ClaudeCode, Codex, Local, Fake, Command)}

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "claude": {"kind": "claude_code", "label": "Claude Code"},
    "codex": {"kind": "codex", "label": "Codex"},
    "local": {"kind": "local", "label": "Local model (MLX)", "base_url": "http://127.0.0.1:8080"},
}

#: what a provider can be asked to do; routing rows name what they need from it
ABILITIES = ("text", "tools", "web", "vision", "image", "image-edit")

#: what each kind can do unless its entry says otherwise with its own `can`
CAN: Dict[str, List[str]] = {
    "claude_code": ["text", "tools", "web", "vision"],
    "codex": ["text", "tools", "web"],
    "local": ["text"],
    "command": [],
    "fake": ["text", "tools"],
}


def capabilities(name: str, cfg: Dict[str, Any]) -> List[str]:
    """What provider `name` can do: the entry's own `can` if it has one,
    else its kind's default (a fake with "harness": false is a bare model)."""
    if isinstance(cfg.get("can"), list):
        return list(cfg["can"])
    kind = cfg.get("kind", "")
    if kind == "fake" and not cfg.get("harness", True):
        return ["text"]
    return list(CAN.get(kind, []))


#: always there, never in the routing table: reached only when picked by name
BUILTIN: Dict[str, Dict[str, Any]] = {
    "command": {"kind": "command", "label": "a command in a folder"},
}


def config() -> Dict[str, Dict[str, Any]]:
    path = paths.config("providers")
    if not path.exists():
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    got = json.loads(path.read_text())
    return {**BUILTIN, **got}


def build(name: str, cfg: Dict[str, Any]) -> Provider:
    cls = KINDS.get(cfg.get("kind", ""))
    if cls is None:
        raise KeyError(f"provider {name}: unknown kind {cfg.get('kind')!r}")
    return cls(name, cfg)


def all_providers() -> Dict[str, Provider]:
    return {name: build(name, cfg) for name, cfg in config().items() if not cfg.get("off")}


def get(name: str) -> Provider:
    cfg = config().get(name)
    if cfg is None:
        raise KeyError(f"no provider named {name!r}")
    return build(name, cfg)


__all__ = ["Outcome", "Provider", "Turn", "all_providers", "get", "config", "KINDS",
           "ABILITIES", "CAN", "capabilities"]
