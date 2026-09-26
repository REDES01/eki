"""The providers on this Mac, from `providers.json` in EKI_HOME.

The file is written with defaults on first use and is yours to edit:

    {"claude": {"kind": "claude_code", "label": "Claude Code"},
     "codex":  {"kind": "codex", "label": "Codex"},
     "local":  {"kind": "local", "base_url": "http://127.0.0.1:8080"}}

ComfyUI (pictures) is never written there. When the file has no "comfyui"
entry and ComfyUI is on this Mac (`comfyui_dir()`: EKI_COMFYUI_DIR, else
~/flux/ComfyUI, holding main.py), `config()` adds the default entry in
memory only. An entry you write, even {"comfyui": {"off": true}}, wins.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from .base import Outcome, Provider, Turn
from .claude_code import ClaudeCode
from .codex import Codex
from .comfyui import Comfyui
from .command import Command
from .fake import Fake
from .local import Local

KINDS = {cls.kind: cls for cls in (ClaudeCode, Codex, Local, Comfyui, Fake, Command)}

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "claude": {"kind": "claude_code", "label": "Claude Code"},
    "codex": {"kind": "codex", "label": "Codex"},
    "local": {"kind": "local", "label": "Local model (MLX)", "base_url": "http://127.0.0.1:8080"},
    # mirrors ~/flux/start.sh without the browser; joins config() only in memory
    "comfyui": {"kind": "comfyui", "label": "ComfyUI (pictures)", "base_url": "http://127.0.0.1:8188",
                "serve": {"command": ["~/flux/venv/bin/python", "main.py", "--output-directory",
                                      "~/flux/output", "--port", "8188"],
                          "cwd": "~/flux/ComfyUI",
                          "env": {"PYTORCH_ENABLE_MPS_FALLBACK": "1", "EKI_STARTED": "1"}},
                "idle_stop": 5, "about": "Draws, edits and upscales pictures on this Mac."},
}

#: defaults that are never written to providers.json, only joined in memory
UNWRITTEN = ("comfyui",)

#: what a provider can be asked to do; routing rows name what they need from it
ABILITIES = ("text", "tools", "web", "vision", "image", "image-edit")

#: what each kind can do unless its entry says otherwise with its own `can`
CAN: Dict[str, List[str]] = {
    "claude_code": ["text", "tools", "web", "vision"],
    "codex": ["text", "tools", "web", "vision"],
    "local": ["text"],
    "comfyui": ["image", "image-edit"],
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


def comfyui_dir() -> Optional[Path]:
    """Where ComfyUI lives on this Mac, if it does: EKI_COMFYUI_DIR, else ~/flux/ComfyUI."""
    d = Path(os.environ.get("EKI_COMFYUI_DIR") or "~/flux/ComfyUI").expanduser()
    return d if (d / "main.py").is_file() else None


def config() -> Dict[str, Dict[str, Any]]:
    path = paths.config("providers")
    if not path.exists():
        written = {n: c for n, c in DEFAULTS.items() if n not in UNWRITTEN}
        path.write_text(json.dumps(written, indent=2) + "\n")
    got = json.loads(path.read_text())
    if "comfyui" not in got:
        where = comfyui_dir()
        if where is not None:
            entry = copy.deepcopy(DEFAULTS["comfyui"])
            entry["serve"]["cwd"] = str(where)
            got["comfyui"] = entry
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
           "ABILITIES", "CAN", "capabilities", "comfyui_dir"]
