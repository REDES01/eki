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
    source TEXT NOT NULL DEFAULT 'ask',     -- ask | roadmap | fault | journal
    owner TEXT NOT NULL DEFAULT 'you',      -- you | eki
    state TEXT NOT NULL DEFAULT 'planning', -- drafting | planning | planned | left | failed
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
    state TEXT NOT NULL DEFAULT 'waiting',  -- waiting | building | waiting on calls | judging | fixing | proposed | applied | locked | queued | resolving | rechecking | landed | live | rolled back | unfit | left | dropped
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
    kind TEXT NOT NULL,                     -- fault | handoff | correction | limit | run | regression | transient
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
CREATE TABLE IF NOT EXISTS chores (         -- a local-model job eki gives itself (eki/chores.py)
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                     -- review | digest | brief | prcomment
    subject TEXT NOT NULL,                  -- an item id, a digest page path, a goal id, or <item id>:<ISO time>
    run_id TEXT,
    thread_id TEXT,
    state TEXT NOT NULL DEFAULT 'open',     -- open | done | failed | skipped
    result TEXT,                            -- the parsed text
    created_at REAL NOT NULL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS chores_kind ON chores(kind, state);
CREATE TABLE IF NOT EXISTS projects (       -- a person's own git folder eki works on (eki/projects.py)
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL,                   -- items are based on it; eki merges into it under 'apply'
    check_cmd TEXT,                         -- gate 1, a shell command; NULL: bin/check if there, else none
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS standing (       -- a standing goal, worked on in rounds
    id TEXT PRIMARY KEY,
    project TEXT,                           -- NULL: eki itself
    text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'on',       -- on | paused | stuck | dropped
    why TEXT,                               -- why no round opened last time
    rest_until REAL,
    failures INTEGER NOT NULL DEFAULT 0,    -- failed rounds in a row
    rounds INTEGER NOT NULL DEFAULT 0,
    last_round_at REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (        -- a push to the person's phone, sent at most once (eki/notify.py)
    key TEXT PRIMARY KEY,                   -- goal id + done, item id + state, ask id
    kind TEXT NOT NULL,                     -- goal_done | needs_you
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    click TEXT,                             -- the URL a tap opens
    created_at REAL NOT NULL,
    sending_at REAL,                        -- claimed by a drain; claimed again after 60 s
    sent_at REAL,
    tries INTEGER NOT NULL DEFAULT 0,
    error TEXT                              -- never holds the topic
);
CREATE TABLE IF NOT EXISTS provider_models (  -- what each program says it offers (eki/modellist.py)
    provider TEXT NOT NULL,
    model TEXT NOT NULL,                    -- the id to ask for
    label TEXT,                             -- the program's display name
    is_default INTEGER NOT NULL DEFAULT 0,
    read_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS provider_models_by ON provider_models(provider);
CREATE TABLE IF NOT EXISTS role_calls (     -- one call made for one of eki's own jobs (eki/roles.py)
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,                     -- router | planner | reviewer | chores
    target TEXT NOT NULL,
    model TEXT,
    ms REAL,
    ok INTEGER NOT NULL DEFAULT 1,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS role_calls_by ON role_calls(role, target, id);
CREATE TABLE IF NOT EXISTS chat_groups (    -- your own groups of chats in the sidebar (not `group`: an SQL keyword)
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ord REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (      -- a prompt sent on a clock (eki/schedule.py)
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    project TEXT,                           -- projects.id or "eki"; NULL: no project
    cadence TEXT NOT NULL,                  -- JSON: {"every": hourly|daily|weekdays|weekly, ...}
    tz TEXT NOT NULL,                       -- the IANA zone, fixed when added
    target TEXT,                            -- JSON targets.fmt; NULL: Auto
    state TEXT NOT NULL DEFAULT 'on',       -- on | paused | dropped
    next_at REAL,
    last_at REAL,
    last_thread TEXT,
    why TEXT,                               -- why the last firing was skipped
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sensitive (path TEXT PRIMARY KEY, state TEXT NOT NULL, by TEXT NOT NULL, score REAL, at REAL NOT NULL);
"""


#: columns added after the first release — the schema only ever grows, so a
#: worker from an older build keeps writing to a file a newer engine opened
ADDED = [
    ("runs", "build", "TEXT"),          # the build the worker ran from
    ("items", "queued_at", "REAL"),     # queue order
    ("items", "head", "TEXT"),          # the predicted head last rebased onto (or resolving against)
    ("items", "rebased", "TEXT"),       # the item's commit on top of head
    ("items", "resolved_on", "TEXT"),   # the commit right under the item's own commits after a resolve: the head it was resolved onto, moved by each re-stack
    ("items", "gate2_run", "TEXT"),     # the gate-2 command run
    ("items", "gate2_on", "TEXT"),      # the rebased sha gate2_run judges
    ("items", "gate2", "TEXT"),         # green | red | NULL
    ("items", "landed_at", "REAL"),
    ("items", "build", "TEXT"),         # the build that carried it live
    ("items", "locked", "TEXT"),        # JSON list of hard-locked files it touches
    ("runs", "attachments", "TEXT"),    # JSON list of absolute paths (pictures) sent with it
    ("goals", "wish", "TEXT"),          # what the person typed
    ("goals", "draft_run", "TEXT"),     # the run that drafts the goal from the wish
    ("goals", "drafted_at", "REAL"),
    ("runs", "model", "TEXT"),          # a per-run model override (the provider's default when NULL)
    ("items", "review_run", "TEXT"),    # the review chore's run
    ("items", "review", "TEXT"),        # ok | no: <why> | none: <why>
    ("items", "reviews", "INTEGER DEFAULT 0"),   # rebuilds a review's "no" asked for
    ("goals", "pick_key", "TEXT"),      # what eki picked: fault:<k>, journal:handoff:answer, roadmap:<key>
    ("goals", "why", "TEXT"),           # why eki picked it
    ("runs", "retries", "INTEGER NOT NULL DEFAULT 0"),   # the transient-error retries it has had
    ("goals", "project", "TEXT"),       # projects.id; NULL: eki itself
    ("goals", "standing_id", "TEXT"),   # the standing goal this is a round of
    ("goals", "shape", "TEXT"),         # JSON: the plan's items, depth, width, chained
    ("items", "why", "TEXT"),           # why it is still waiting
    ("goals", "issue", "INTEGER"),      # the GitHub issue it came from (eki/issues.py)
    ("standing", "issue", "INTEGER"),
    ("projects", "issues", "INTEGER DEFAULT 0"),   # was: issues watched; nothing reads it any more
    ("items", "pr", "TEXT"),            # the pull request's URL (eki/prs.py)
    ("items", "pr_state", "TEXT"),      # open | merged | closed
    ("items", "pushed", "TEXT"),        # the sha last pushed to eki/<id> on origin
    ("items", "pr_seen", "TEXT"),       # ISO-8601 UTC time of the newest PR comment looked at
    ("items", "followups", "INTEGER DEFAULT 0"),   # changes made for comments on the PR
    ("projects", "repo", "TEXT"),       # OWNER/REPO; NULL on the local path
    ("projects", "state", "TEXT DEFAULT 'on'"),    # on | retired | dropped | merged | deleting | deleted
    ("projects", "setup", "TEXT"),      # NULL (local) | cloning | installing | ready | fault
    ("projects", "setup_run", "TEXT"),  # the clone or install command run
    ("projects", "fault", "TEXT"),      # "<clone|install> failed: <why>"
    ("projects", "check_from", "TEXT"), # set | guessed: <why> | none: <why>
    ("projects", "install_cmd", "TEXT"),   # run once in the clone before the first goal
    ("projects", "ignored", "TEXT"),    # JSON list of issue numbers open when it was dropped
    ("items", "judged_by", "TEXT"),     # the check that judged it, and where it came from
    ("projects", "folder", "TEXT"),     # your real folder, where its chats run (eki/projectchats.py)
    ("threads", "project", "TEXT"),     # projects.id or "eki"; NULL: a chat outside any project
    ("provider_models", "description", "TEXT"),      # the program's own text
    ("provider_models", "resolved", "TEXT"),         # the pinned model an alias stands for
    ("provider_models", "efforts", "TEXT"),          # JSON list of levels; NULL: it takes no effort
    ("provider_models", "default_effort", "TEXT"),   # NULL: the program doesn't say
    ("runs", "effort", "TEXT"),         # a per-run effort (the model's default when NULL)
    ("runs", "fresh", "INTEGER NOT NULL DEFAULT 0"),   # 1: a new program session; resumes only its own
    ("threads", "by", "TEXT"),          # NULL: a chat of yours; 'eki': a thread eki made for itself
    ("threads", "title_by", "TEXT DEFAULT 'old'"),   # old (never retitled) | prompt | eki | you
    ("threads", "pinned_at", "REAL"),   # pinned when not NULL
    ("threads", "seen_at", "REAL"),     # when you last looked; NULL reads as seen
    ("threads", "unread", "INTEGER DEFAULT 0"),      # marked unread by hand
    ("threads", "archived_at", "REAL"),
    ("threads", "chat_group", "TEXT"),  # chat_groups.id
    ("threads", "deleted_at", "REAL"),  # hidden at once; the rows go once nothing is active
    ("threads", "schedule", "TEXT"),    # schedules.id a firing's chat came from
    ("projects", "description", "TEXT"),   # what you're trying to achieve; eki's is fixed
    ("projects", "autonomy", "TEXT"),   # apply | propose; NULL: the default (eki/projects.py)
    ("items", "landed_on", "TEXT"),     # the project branch eki landed it on; NULL otherwise
    ("items", "context", "TEXT"),       # JSON [{name, why}] knowledge cards the planner picked; NULL: all
    ("items", "waits", "TEXT"),         # JSON {stage, run, since, calls} while waiting on calls
    ("projects", "lands", "TEXT"),      # here | github; NULL: not said yet (eki/projectlands.py)
    ("projects", "chat_history", "INTEGER DEFAULT 1"),   # 1: chat turns are committed (eki/chatcommit.py)
    ("goals", "hot", "TEXT"),           # JSON: the plan's hot spots, [{file, items, generated}]
    ("goals", "dry_run", "TEXT"),       # the dry build's command run (eki/drybuild.py)
    ("items", "fixer", "TEXT"),         # JSON {stage, failure, run, at, head, summary}: its one fixer run (eki/fixer.py)
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
