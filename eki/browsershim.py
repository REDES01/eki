"""The browser shim: Playwright's browsers folder, mirrored so each browser
executable waits for a browser slot (browserslots.py) before it starts.

Every run eki starts gets PLAYWRIGHT_BROWSERS_PATH=mirror(), so Playwright's
normal lookup — in scripts and in the Playwright MCP — goes through it. The
executable is the one place every browser launch passes, whoever asked for it.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import paths


def mirror() -> Path:
    return paths.home() / "browsers" / "playwright"


def real() -> Path:
    """Playwright's own browsers folder: $EKI_PLAYWRIGHT_CACHE, else eki's own
    $PLAYWRIGHT_BROWSERS_PATH unless it is the mirror, else the default cache."""
    given = os.environ.get("EKI_PLAYWRIGHT_CACHE")
    if given:
        return Path(given).expanduser()
    given = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if given and given != "0" and Path(given).expanduser() != mirror():
        return Path(given).expanduser()
    return paths.user_home() / "Library/Caches/ms-playwright"


def ensure() -> Path:
    """The mirror folder, made if missing."""
    p = mirror()
    p.mkdir(parents=True, exist_ok=True)
    return p
