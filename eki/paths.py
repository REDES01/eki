"""Where everything lives.

One directory, `EKI_HOME`. While the old eki still runs on this Mac the
default is `~/.eki-next`, so the two never share a file; at the swap it
becomes `~/.eki`.
"""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("EKI_HOME") or "~/.eki-next").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def db() -> Path:
    return home() / "eki.db"


def logs() -> Path:
    p = home() / "logs"
    p.mkdir(exist_ok=True)
    return p


def engine_lock() -> Path:
    return home() / "engine.lock"


def skills() -> Path:
    p = home() / "skills"
    p.mkdir(exist_ok=True)
    return p


def config(name: str) -> Path:
    """A JSON file the person may read and edit: providers, routing, mcp."""
    return home() / f"{name}.json"


def scratch() -> Path:
    """Where a run with no folder works."""
    p = home() / "scratch"
    p.mkdir(exist_ok=True)
    return p
