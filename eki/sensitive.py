"""Pictures that shouldn't be on screen: a per-picture 'sensitive' mark.

The mark lives in the store's `sensitive` table, one row per picture path
(absolute, normpath'd). `state` is 'on' (sensitive) or 'off' (looked at, not
sensitive); `by` is 'person' or 'auto' (the local classifier). A person's
mark always wins: an auto mark never overwrites it. A sensitive picture is
never synced or exported (see `skillout`).
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional, Set

BY = ("person", "auto")


def norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def mark(conn: sqlite3.Connection, path: str, on: bool, by: str = "person",
         score: Optional[float] = None) -> bool:
    """Write the mark for `path`; an auto mark leaves a person's alone. Whether it wrote."""
    if by not in BY:
        raise ValueError(f"unknown marker {by!r}: person or auto")
    path = norm(path)
    if by == "auto":
        row = conn.execute("SELECT by FROM sensitive WHERE path = ?", (path,)).fetchone()
        if row and row["by"] == "person":
            return False
    conn.execute("INSERT OR REPLACE INTO sensitive(path, state, by, score, at) VALUES (?,?,?,?,?)",
                 (path, "on" if on else "off", by, score, time.time()))
    return True


def is_sensitive(conn: sqlite3.Connection, path: str) -> bool:
    row = conn.execute("SELECT state FROM sensitive WHERE path = ?", (norm(path),)).fetchone()
    return bool(row) and row["state"] == "on"


def paths(conn: sqlite3.Connection) -> Set[str]:
    """Every path marked sensitive."""
    return {r["path"] for r in conn.execute("SELECT path FROM sensitive WHERE state = 'on'")}


def seen(conn: sqlite3.Connection, path: str) -> bool:
    """Any mark at all, on or off, by anyone."""
    return conn.execute("SELECT 1 FROM sensitive WHERE path = ?", (norm(path),)).fetchone() is not None
