"""The one source of truth: a SQLite file every process opens itself.

The engine, each worker and the command line all read and write it
directly, so none of them depends on another being up. WAL mode lets
readers and one writer work at once; the busy timeout covers the rest.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    provider TEXT,               -- who the thread stays with
    cwd TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    thread_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seen_run TEXT,               -- the last run of this thread the session has taken part in
    PRIMARY KEY (thread_id, provider)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,        -- order within the thread
    prompt TEXT NOT NULL,
    provider TEXT,               -- NULL until routed
    pinned INTEGER NOT NULL DEFAULT 0,
    row TEXT,
    why TEXT,
    exclude TEXT NOT NULL DEFAULT '[]',
    priority TEXT NOT NULL DEFAULT 'now',
    state TEXT NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    child_pid INTEGER,           -- the program the worker started (its own process group)
    heartbeat REAL,
    spawned_at REAL,
    retry_at REAL,               -- a waiting run isn't tried again before this
    cancel INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    parent TEXT,                 -- the run this one continues (a handoff)
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, seq);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    t REAL NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS cooldowns (
    provider TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(paths.db(), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction, taken up front so two writers never deadlock."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def now() -> float:
    return time.time()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
