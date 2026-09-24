# SPDX-License-Identifier: Apache-2.0
"""Projects: a folder with a `.eki/` in it.

A project is the unit eki's later work hangs on — its chats, its roster, its
memory (ROADMAP Stage 5). What makes a folder one is a `.eki/` folder inside
it, the way `.git/` makes a repository: made once (`eki project init`), kept
in the repo if you like, and found again by walking up from wherever a call
is made. So `eki ask` from `game/src/npc/` belongs to `game/` when `game/.eki/`
is there, and a call with no such folder above it belongs to no project.

Your home folder's `.eki` is eki's own store, not a project marker; the walk
stops before it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

MARKER = ".eki"
#: what `init` writes, so the marker survives git (an empty folder doesn't)
#: and a project can be given a name other than its folder's
ABOUT = "project.json"


def _home() -> Path:
    return Path.home().resolve()


def find(folder: str) -> Optional[Path]:
    """The project `folder` is in: the nearest folder up, itself included,
    with a `.eki/` folder — never your home folder or above it. None when
    there is none."""
    if not folder:
        return None
    try:
        start = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if start.is_file():
        start = start.parent
    home = _home()
    for d in (start, *start.parents):
        if d == home or d == Path(d.anchor):
            return None
        if (d / MARKER).is_dir():
            return d
    return None


def name(root: Path) -> str:
    """What a project is called: `name` in its project.json, else its folder's."""
    try:
        about = json.loads((root / MARKER / ABOUT).read_text())
        given = str(about.get("name") or "").strip() if isinstance(about, dict) else ""
    except (OSError, ValueError):
        given = ""
    return given or root.name


def describe(folder: str) -> Dict[str, Any]:
    """{"root", "name"} for the project `folder` is in; empty when none."""
    root = find(folder)
    return {"root": str(root), "name": name(root)} if root else {}


def init(folder: str, called: str = "") -> Dict[str, Any]:
    """Make `folder` a project: its `.eki/` and a project.json naming it.
    Again on a project only renames it, when `called` is given."""
    root = Path(folder or ".").expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"no such folder: {root}")
    if root == _home() or root in _home().parents:
        raise ValueError("your home folder's .eki is eki's own store; pick a folder inside it")
    marker = root / MARKER
    made = not marker.is_dir()
    marker.mkdir(exist_ok=True)
    about = marker / ABOUT
    if made or called or not about.exists():
        about.write_text(json.dumps({"name": called.strip() or name(root)}, indent=2) + "\n")
    return {"root": str(root), "name": name(root), "made": made}
