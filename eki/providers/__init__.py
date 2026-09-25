"""The providers on this Mac, from `providers.json` in EKI_HOME.

The file is written with defaults on first use and is yours to edit:

    {"claude": {"kind": "claude_code", "label": "Claude Code"},
     "codex":  {"kind": "codex", "label": "Codex"},
     "local":  {"kind": "local", "base_url": "http://127.0.0.1:8080"}}
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .. import paths
from .base import Outcome, Provider, Turn
from .claude_code import ClaudeCode
from .codex import Codex
from .fake import Fake
from .local import Local

KINDS = {cls.kind: cls for cls in (ClaudeCode, Codex, Local, Fake)}

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "claude": {"kind": "claude_code", "label": "Claude Code"},
    "codex": {"kind": "codex", "label": "Codex"},
    "local": {"kind": "local", "label": "Local model (MLX)", "base_url": "http://127.0.0.1:8080"},
}


def config() -> Dict[str, Dict[str, Any]]:
    path = paths.config("providers")
    if not path.exists():
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    return json.loads(path.read_text())


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


__all__ = ["Outcome", "Provider", "Turn", "all_providers", "get", "config", "KINDS"]
