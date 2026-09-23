# SPDX-License-Identifier: Apache-2.0
"""Runs: the only way anything happens.

There is no such thing as a chat turn that lives in a window. Every request —
a one-line question, a ten-minute refactor — becomes a row here, executes in
the engine, and streams to whoever is watching. Close the window mid-answer
and the answer still finishes; open it again and you are looking at the same
run, replayed from the start and then live.

Runs execute in parallel. The engine is a machine you hand work to, and a
quick question has no business waiting behind a refactor.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    prompt          TEXT NOT NULL,
    cwd             TEXT NOT NULL DEFAULT '',
    requested       TEXT NOT NULL DEFAULT '',   -- backend the user forced, if any
    backend         TEXT,                       -- backend the router chose
    reason          TEXT,
    state           TEXT NOT NULL,              -- queued running done failed
                                                -- cancelled interrupted
    output          TEXT NOT NULL DEFAULT '',
    error           TEXT,
    images          INTEGER NOT NULL DEFAULT 0,
    -- the question this run answers; history is everything up to it, so
    -- parallel runs in one conversation never see each other's later turns
    user_turn       INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    started_at      INTEGER,
    ended_at        INTEGER
);
CREATE INDEX IF NOT EXISTS runs_by_state ON runs (state, created_at);
CREATE INDEX IF NOT EXISTS runs_by_conversation ON runs (conversation_id, created_at);
"""

TERMINAL = ("done", "failed", "cancelled", "interrupted")
LIVE = ("queued", "running")


def unseen(seen: int, text: str, end: int) -> str:
    """The part of a live chunk a watcher doesn't already have.

    A watcher joining mid-run has read the stored log up to `seen`; a live
    chunk covers [end - len(text), end). Anything below `seen` is a repeat.
    """
    if end <= seen:
        return ""
    start = end - len(text)
    return text[seen - start:] if start < seen else text


class RunStore:
    def __init__(self, path: Path | str, owner: bool = False):
        """`owner` is the one process that executes runs — the engine.

        Only the owner may declare live runs dead on startup. Anything else
        opening this file (`eki history`, a test) would otherwise mark the
        running engine's work as interrupted while it was still happening.
        """
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(runs)")}
        # "ask" answers a question; "deploy" downloads and sets up a model
        if "kind" not in cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'ask'")
        if "label" not in cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN label TEXT NOT NULL DEFAULT ''")
        if "payload" not in cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN payload TEXT NOT NULL DEFAULT ''")
        self._adopt_old_jobs()
        #: runs this engine found still "live" from a previous one, now
        #: interrupted — the engine writes a note into their threads
        self.just_interrupted: List[Dict[str, Any]] = []
        if owner:
            # anything still live belongs to an engine that is gone
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE state IN ('running','queued')").fetchall()
            self.just_interrupted = [dict(r) for r in rows]
            self._conn.execute(
                "UPDATE runs SET state = 'interrupted', ended_at = ?"
                " WHERE state IN ('running','queued')", (int(time.time()),))
        self._conn.commit()

    def _adopt_old_jobs(self) -> None:
        """Carry over history from when runs were called jobs."""
        tables = {r["name"] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" not in tables:
            return
        if self._conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]:
            return
        self._conn.execute(
            "INSERT INTO runs (id, prompt, cwd, backend, reason, state, output,"
            " error, created_at, started_at, ended_at)"
            " SELECT id, prompt, cwd, backend, reason, state, output, error,"
            " created_at, started_at, ended_at FROM jobs")

    def create(self, prompt: str, *, conversation: str = "", cwd: str = "",
               requested: str = "", images: bool = False, user_turn: int = 0,
               kind: str = "ask", payload: str = "") -> str:
        rid = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, conversation_id, prompt, cwd, requested,"
                " images, user_turn, state, created_at, kind, payload)"
                " VALUES (?,?,?,?,?,?,?,'queued',?,?,?)",
                (rid, conversation, prompt, cwd, requested, int(images),
                 user_turn, int(time.time()), kind, payload))
            self._conn.commit()
        return rid

    def update(self, rid: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE runs SET {cols} WHERE id = ?",
                               (*fields.values(), rid))
            self._conn.commit()

    def append_output(self, rid: str, text: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET output = output || ? WHERE id = ?", (text, rid))
            self._conn.commit()

    def get(self, rid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, conversation_id, prompt, backend, state, cwd, kind,"
                " created_at, ended_at, length(output) AS output_len FROM runs"
                # rowid breaks the tie: two runs started in the same second
                # would otherwise come back in whatever order sqlite felt like
                " ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def active(self, conversation: str) -> Optional[Dict[str, Any]]:
        """The run this conversation is waiting on, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE conversation_id = ?"
                " AND state IN ('queued','running')"
                " ORDER BY created_at DESC LIMIT 1", (conversation,)).fetchone()
        return dict(row) if row else None

    def durations(self, backend: str, limit: int = 30) -> List[float]:
        """Seconds the latest finished requests on this backend took."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ended_at - started_at FROM runs WHERE backend = ? AND state = 'done'"
                " AND kind = 'ask' AND started_at > 0 AND ended_at >= started_at"
                " ORDER BY created_at DESC LIMIT ?", (backend, limit)).fetchall()
        return [float(r[0]) for r in rows]

    def finished_count(self, backends: List[str]) -> int:
        """Requests that have ever finished on these backends (a running count)."""
        if not backends:
            return 0
        marks = ",".join("?" for _ in backends)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM runs WHERE backend IN ({marks}) AND state IN ('done','failed')"
                " AND kind = 'ask'", tuple(backends)).fetchone()
        return int(row[0] or 0)

    def last_done(self, conversation: str) -> Optional[Dict[str, Any]]:
        """The newest run in a conversation that finished."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE conversation_id = ? AND state = 'done'"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1", (conversation,)).fetchone()
        return dict(row) if row else None

    def live(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, conversation_id, backend, state FROM runs"
                " WHERE state IN ('queued','running')").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Runner:
    """Starts runs and keeps track of the ones in flight.

    No worker loop and no queue: a submitted run starts immediately. What this
    class really owns is cancellation — killing the task in a way that also
    kills the child process — and the fan-out to watchers.
    """

    def __init__(self, store: RunStore, dispatch: Callable):
        self.store = store
        self.dispatch = dispatch                 # async (run) -> AsyncIterator[str]
        self.tasks: Dict[str, asyncio.Task] = {}
        self._listeners: Dict[str, List[asyncio.Queue]] = {}
        #: activity and prompts of live runs, replayed to a watcher who joins late
        self.activity: Dict[str, List[Dict[str, Any]]] = {}
        #: the engine is going away (a restart, a swap): runs cut off now
        #: aren't cancelled by anyone — the next engine finds them still
        #: "running", marks them interrupted, and carries them on
        self.stopping = False
        #: (run, exception) for a run that ended in an error — the engine
        #: tells eki's faults apart from a provider saying no (eki/observe.py)
        self.on_error: Optional[Callable[[Dict[str, Any], BaseException], None]] = None

    @property
    def running(self) -> List[str]:
        return [rid for rid, task in self.tasks.items() if not task.done()]

    # ---- watching ----------------------------------------------------

    def subscribe(self, rid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.setdefault(rid, []).append(q)
        return q

    def unsubscribe(self, rid: str, q: asyncio.Queue) -> None:
        listeners = self._listeners.get(rid) or []
        if q in listeners:
            listeners.remove(q)
        if not listeners:
            self._listeners.pop(rid, None)

    def _publish(self, rid: str, event: Dict[str, Any]) -> None:
        for q in list(self._listeners.get(rid) or []):
            q.put_nowait(event)

    def _state(self, rid: str, state: str, **extra: Any) -> None:
        """One place to move a run's state, so watchers never miss the move."""
        self.store.update(rid, state=state, **extra)
        self._publish(rid, {"event": "state", "state": state,
                            **{k: v for k, v in extra.items() if k != "error"}})

    # ---- running -----------------------------------------------------

    async def submit(self, rid: str) -> None:
        run = self.store.get(rid)
        if not run or run["state"] != "queued":
            return
        self.tasks[rid] = asyncio.create_task(self._execute(run))

    def cancel(self, rid: str) -> bool:
        task = self.tasks.get(rid)
        if task and not task.done():
            task.cancel()
            return True
        run = self.store.get(rid)
        if run and run["state"] == "queued":
            self._state(rid, "cancelled", ended_at=int(time.time()))
            return True
        return False

    async def _execute(self, run: Dict[str, Any]) -> None:
        rid = run["id"]
        self._state(rid, "running", started_at=int(time.time()))
        stream = self.dispatch(run)
        # Every output event carries where it ends in the log. A watcher that
        # joins mid-run reads the stored log first and then the live feed;
        # the offsets are how it knows which live chunks the log already had.
        offset = 0
        try:
            async for chunk in stream:
                if isinstance(chunk, dict) and "kind" in chunk:
                    # what the program is doing, or what it's asking — shown
                    # in the thread, kept for a watcher who joins late
                    event = {"event": chunk["kind"], **{k: v for k, v in chunk.items() if k != "kind"}}
                    self.activity.setdefault(rid, []).append(event)
                    self._publish(rid, event)
                    continue
                if isinstance(chunk, dict):       # a routing note, not an answer
                    self.store.update(rid, backend=chunk.get("backend"),
                                      reason=chunk.get("reason"))
                    self._publish(rid, {"event": "route", **chunk})
                    continue
                self.store.append_output(rid, chunk)
                offset += len(chunk)
                self._publish(rid, {"event": "output", "text": chunk, "end": offset})
            self._state(rid, "done", ended_at=int(time.time()))
        except asyncio.CancelledError:
            # close the generator explicitly: its `finally` is what terminates
            # the child process, and letting the GC do it leaves the CLI running
            try:
                await stream.aclose()
            except Exception:                     # noqa: BLE001
                pass                              # cancellation is the outcome
            if not self.stopping:
                self._state(rid, "cancelled", ended_at=int(time.time()))
            raise
        except Exception as e:                    # noqa: BLE001
            if self.on_error is not None:
                try:
                    self.on_error(run, e)
                except Exception:                 # noqa: BLE001
                    pass                          # noticing never fails a run twice
            self.store.update(rid, error=str(e)[:500])
            # the reason before the state: a watcher stops at a terminal
            # state, so anything published after it is never read
            self._publish(rid, {"event": "error", "message": str(e)[:500]})
            self._state(rid, "failed", ended_at=int(time.time()))
        finally:
            self.tasks.pop(rid, None)
            self.activity.pop(rid, None)

    async def stop(self) -> None:
        """Shutdown: nothing is left orphaned. Runs cut off here stay
        "running" in the store for the next engine to pick up."""
        self.stopping = True
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
