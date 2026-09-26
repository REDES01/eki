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
CREATE TABLE IF NOT EXISTS asks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- question | permission | form
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',   -- open | answered | delivered | withdrawn
    answer TEXT,
    created_at REAL NOT NULL,
    answered_at REAL
);
CREATE INDEX IF NOT EXISTS asks_run ON asks(run_id, state);
CREATE TABLE IF NOT EXISTS quota (
    provider TEXT PRIMARY KEY,
    windows TEXT NOT NULL,       -- {"five_hour": {"used": 0..1, "resets_at": epoch}, …}
    plan TEXT,
    observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cooldowns (
    provider TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (          -- eki builds eki: what was asked for
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ask',     -- ask | roadmap | fault
    owner TEXT NOT NULL DEFAULT 'you',      -- you | eki
    state TEXT NOT NULL DEFAULT 'planning', -- planning | planned | failed
    thread_id TEXT,
    plan_run TEXT,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS items (          -- one piece of a goal, built in a worktree of its own
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    title TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',       -- the declared write-set (globs)
    deps TEXT NOT NULL DEFAULT '[]',        -- item ids that must be fit first
    independent INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'waiting',  -- waiting | building | judging | proposed | applied | locked | queued | resolving | rechecking | landed | live | rolled back | unfit | left | dropped
    thread_id TEXT,
    worktree TEXT,
    branch TEXT,
    base TEXT,                              -- the commit the worktree started from
    commit_sha TEXT,                        -- what the agent left, committed by eki
    touched TEXT NOT NULL DEFAULT '[]',     -- files really changed
    run_id TEXT,                            -- the run under way, or the last one
    tries INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    verdict TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS items_state ON items(state);
CREATE TABLE IF NOT EXISTS journal (        -- what happened to eki, written by eki/observe.py only
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL,
    kind TEXT NOT NULL,                     -- fault | handoff | correction | limit | run | regression
    run_id TEXT,
    thread_id TEXT,
    provider TEXT,
    build TEXT,                             -- the build that was running
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS journal_t ON journal(t);
CREATE INDEX IF NOT EXISTS journal_kind ON journal(kind, t);
CREATE TABLE IF NOT EXISTS build_scores (   -- gate 4: the score before and after a build went healthy
    build TEXT PRIMARY KEY,
    healthy_at REAL NOT NULL,
    before TEXT,                            -- JSON score
    after TEXT,                             -- JSON score, recomputed until frozen
    measured_at REAL,
    verdict TEXT                            -- better | same | worse; NULL until after is frozen
);
"""


#: columns added after the first release — the schema only ever grows, so a
#: worker from an older build keeps writing to a file a newer engine opened
ADDED = [
    ("runs", "build", "TEXT"),          # the build the worker ran from
    ("items", "queued_at", "REAL"),     # queue order
    ("items", "head", "TEXT"),          # the predicted head last rebased onto (or resolving against)
    ("items", "rebased", "TEXT"),       # the item's commit on top of head
    ("items", "gate2_run", "TEXT"),     # the gate-2 command run
    ("items", "gate2_on", "TEXT"),      # the rebased sha gate2_run judges
    ("items", "gate2", "TEXT"),         # green | red | NULL
    ("items", "landed_at", "REAL"),
    ("items", "build", "TEXT"),         # the build that carried it live
    ("items", "locked", "TEXT"),        # JSON list of hard-locked files it touches
    ("runs", "attachments", "TEXT"),    # JSON list of absolute paths (pictures) sent with it
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(paths.db(), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    for table, col, decl in ADDED:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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
