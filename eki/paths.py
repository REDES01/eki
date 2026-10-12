"""Where everything lives.

One directory, `EKI_HOME`, by default `~/.eki`. (The eki before the
rebuild is kept, dated, in `~/.eki-2026-09-26` and `~/eki-2026-09-26`.)
"""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("EKI_HOME") or "~/.eki").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_home() -> Path:
    """The person's own home, where their project folders live (`~/<name>`)."""
    return Path(os.environ.get("EKI_USER_HOME") or Path.home())


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


def never_index(p: Path) -> Path:
    """Folder `p`, made if missing, with a `.metadata_never_index` marker so
    Spotlight leaves it alone (eki makes and deletes folders in it all day)."""
    p.mkdir(parents=True, exist_ok=True)
    marker = p / ".metadata_never_index"
    if not marker.exists():
        marker.touch()
    return p


def work() -> Path:
    """Worktrees for runs that must not collide (eki/workspace.py). A folder
    whose name ends in .noindex is never indexed by Spotlight; the old
    `work` becomes a symlink to it (eki/noindex.py)."""
    p = home() / "work.noindex"
    p.mkdir(exist_ok=True)
    return p


def scratch() -> Path:
    """Where a run with no folder works."""
    return never_index(home() / "scratch")


def attachments() -> Path:
    """Pictures uploaded from the window, and safe copies made at intake."""
    p = home() / "attachments"
    p.mkdir(exist_ok=True)
    return p


def images() -> Path:
    """Pictures a drawing program made, one folder per run."""
    p = home() / "images"
    p.mkdir(exist_ok=True)
    return p


def projects() -> Path:
    """eki's own clones of GitHub repos, one per <owner>/<repo>."""
    p = home() / "projects"
    p.mkdir(exist_ok=True)
    return p
