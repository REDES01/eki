"""The person's comments on a PR come back in as follow-ups to the item.

Each housekeeping pass (at most every self.issues_minutes) reads new
comments by the gh user on open PRs, triages them (rules, then a prcomment
chore), and puts an item whose change was requested back to waiting on the
same branch; the new commit is pushed and answered in one line. Not written
yet: the tick does nothing.
"""
from __future__ import annotations

import sqlite3
from typing import List

from . import github


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prfollow"):
        return []
    return []
