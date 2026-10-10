"""A short title for each new chat of yours, written by a `title` chore."""
from __future__ import annotations

import sqlite3
from typing import List


def settle(conn: sqlite3.Connection) -> List[str]:
    """Write each finished title chore's cleaned text where the title is still
    the prompt's (a title you set always wins), then close the chore. (Filled
    in by the titles item.)"""
    return []


def start_due(conn: sqlite3.Connection) -> List[str]:
    """Start a title chore, a few per pass, for each chat of yours titled from
    its prompt with a done answer and no title chore yet. (Filled in by the
    titles item.)"""
    return []
