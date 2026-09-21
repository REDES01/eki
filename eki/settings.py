# SPDX-License-Identifier: Apache-2.0
"""eki's own settings: the few knobs that aren't a provider or a policy.

Kept in ~/.eki/settings.json so both the engine and the CLI read the same
file, and so a user can edit it without the app running.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

HOME = Path("~/.eki").expanduser()
PATH = HOME / "settings.json"

DEFAULTS: Dict[str, Any] = {
    #: the Python that runs mlx_lm — set up by eki, or one the user already has
    "mlx_python": str(HOME / "mlx" / ".venv" / "bin" / "python"),
    "hf_home": os.environ.get("HF_HOME", "~/.cache/huggingface"),
    #: context eki sizes a local model's memory estimate against
    "context_budget": 32768,
    #: provider key of the small model that labels requests, if any
    "router_model": "",
    #: "rules" (free, always on) or "model" (the router model, rules as backup)
    "router": "rules",
    #: let eki open Claude Code's /usage panel to refresh the reading while
    #: you have the app open. /usage asks no model anything, so it's free.
    "claude_probe": False,
    "claude_probe_minutes": 10,
    #: measure providers on their own, with the public benchmark items:
    #: "local" (free: eki's own model servers, when idle), "all" (also
    #: Claude, Codex and API providers, when their windows are nearly idle),
    #: or "off"
    "auto_measure": "local",
    #: keep Claude Code open under eki's interface — streaming, its slash
    #: commands, its questions and permission prompts as cards — rather than
    #: one silent run per message
    "live_claude": True,
    #: the same for Codex, through its app-server (eki/codex_live.py)
    "live_codex": True,
    #: appended to Claude Code's own system prompt
    "claude_system_prompt": "",
    #: "auto": Claude Code and Codex run commands and edit files without
    #: asking (their own "skip permissions" modes); "ask": each one that
    #: needs a say becomes a card in the thread. Questions they ask *you*
    #: always come through.
    "permissions": "auto",
    #: a macOS notification when a scheduled task finishes
    "notify_scheduled": True,
}


def load() -> Dict[str, Any]:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(data: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**load(), **{k: v for k, v in data.items() if k in DEFAULTS}}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(merged, indent=2))
    return merged
