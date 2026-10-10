"""What you do to a chat from the list: pin, unread, rename, move, group, archive, delete."""
from __future__ import annotations

import sqlite3
from typing import List


def purge(conn: sqlite3.Connection) -> List[str]:
    """Remove the events, runs, asks, sessions and row of every deleted thread
    with no active run; one line each. Files are never touched. (Filled in by
    the actions item; nothing to purge yet.)"""
    return []
