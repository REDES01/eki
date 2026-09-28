"""Pull requests out: a proposed item on a GitHub-path project goes back as a PR.

Each housekeeping pass (at most every self.issues_minutes) pushes eki/<id>
to origin and opens (or finds) its pull request, then polls the open ones:
merged on GitHub is applied, closed is dropped. Not written yet: the tick
does nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import github


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prs"):
        return []
    return []
