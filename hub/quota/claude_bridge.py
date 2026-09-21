"""Install or remove the status-line bridge in Claude Code's settings.

This edits ~/.claude/settings.json, which belongs to you, not to hub — so it
is opt-in, it keeps a dated backup before every write, and whatever status
line you already had keeps working: it is moved into a chain file and the
bridge runs it for you. Uninstalling puts it back exactly as it was.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .statusline_bridge import CHAIN, QUOTA_DIR, READING

SETTINGS = Path(os.environ.get("CLAUDE_SETTINGS", "~/.claude/settings.json")).expanduser()
MARKER = "hub.quota.statusline_bridge"      # how we recognise our own entry


def bridge_command(python: Optional[str] = None) -> str:
    """The command Claude Code will run. Absolute paths only: Claude Code
    runs it with its own PATH, which may not include this interpreter."""
    python = python or sys.executable
    root = Path(__file__).resolve().parent.parent.parent
    return (f"cd {shlex.quote(str(root))} && "
            f"{shlex.quote(python)} -m {MARKER}")


def _load() -> Dict[str, Any]:
    try:
        return json.loads(SETTINGS.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise RuntimeError(f"{SETTINGS} isn't valid JSON; not touching it ({e})")


def _save(settings: Dict[str, Any]) -> Path:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    backup = QUOTA_DIR / f"claude-settings.backup-{int(time.time())}.json"
    QUOTA_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup.write_text(SETTINGS.read_text())
    fd, tmp = tempfile.mkstemp(dir=SETTINGS.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS)
    return backup


def installed() -> bool:
    line = _load().get("statusLine") or {}
    return MARKER in str(line.get("command", ""))


def install(python: Optional[str] = None) -> str:
    settings = _load()
    current = settings.get("statusLine")
    if current and MARKER in str(current.get("command", "")):
        return "already installed"
    if current:
        # keep theirs: the bridge runs it and prints its output
        QUOTA_DIR.mkdir(parents=True, exist_ok=True)
        CHAIN.write_text(json.dumps({"command": current.get("command", ""),
                                     "original": current}))
    else:
        CHAIN.unlink(missing_ok=True)
    settings["statusLine"] = {"type": "command", "command": bridge_command(python)}
    backup = _save(settings)
    kept = " (your existing status line still shows)" if current else ""
    return f"installed{kept}; backup at {backup}"


def uninstall() -> str:
    settings = _load()
    current = settings.get("statusLine") or {}
    if MARKER not in str(current.get("command", "")):
        return "not installed"
    try:
        original = json.loads(CHAIN.read_text()).get("original")
    except (OSError, ValueError):
        original = None
    if original:
        settings["statusLine"] = original
    else:
        settings.pop("statusLine", None)
    backup = _save(settings)
    CHAIN.unlink(missing_ok=True)
    return f"removed; your previous status line is restored (backup at {backup})"


def reading() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(READING.read_text())
    except (OSError, ValueError):
        return None
