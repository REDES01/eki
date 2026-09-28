"""Issues in: a watched project's open GitHub issues labelled eki become its goals.

Each housekeeping pass (at most every self.issues_minutes) lists the issues
of every watched project on the GitHub path (eki/github.py). An issue by the
person gh is logged in as, not yet taken, becomes a background goal owned by
eki — or, labelled `standing`, a standing goal. An issue closed on GitHub
drops the unfinished work it started. Not written yet: the tick does nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import github


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("issues"):
        return []
    return []
