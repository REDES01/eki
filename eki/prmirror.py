"""eki's own landed work, mirrored read-only as PRs against eki/landed.

Only when self.pr_mirror is on and the integration repo's origin is on
GitHub: an item that landed opens a PR against eki/landed, closed once it is
live or rolled back. Not written yet: the tick does nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import github


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prmirror"):
        return []
    return []
