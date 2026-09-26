"""Threads, runs and events — the data, read and written in one place."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from .db import dumps, now, tx

ACTIVE = ("queued", "starting", "running")
ENDED = ("done", "failed", "cancelled", "handed_off")


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ---- threads ---------------------------------------------------------------------------

def create_thread(conn: sqlite3.Connection, title: str, cwd: Optional[str]) -> str:
    tid = new_id()
    conn.execute("INSERT INTO threads(id, title, cwd, created_at) VALUES (?,?,?,?)",
                 (tid, title[:80], cwd, now()))
    return tid


def thread(conn: sqlite3.Connection, tid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM threads WHERE id=? OR id LIKE ?",
                        (tid, tid + "%")).fetchone()


def cwd_of(conn: sqlite3.Connection, tid: str) -> Optional[str]:
    row = conn.execute("SELECT cwd FROM threads WHERE id=?", (tid,)).fetchone()
    return row["cwd"] if row and row["cwd"] else None


def session(conn: sqlite3.Connection, tid: str, provider: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM sessions WHERE thread_id=? AND provider=?",
                        (tid, provider)).fetchone()


def save_session(conn: sqlite3.Connection, tid: str, provider: str, sid: str, run_id: str) -> None:
    conn.execute("INSERT INTO sessions(thread_id, provider, session_id, seen_run) VALUES (?,?,?,?) "
                 "ON CONFLICT(thread_id, provider) DO UPDATE SET session_id=excluded.session_id, "
                 "seen_run=excluded.seen_run", (tid, provider, sid, run_id))


# ---- runs ------------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, tid: str, prompt: str, *, provider: Optional[str] = None,
               priority: str = "now", parent: Optional[str] = None,
               exclude: Optional[List[str]] = None, row: Optional[str] = None,
               attachments: Optional[List[str]] = None, model: Optional[str] = None) -> str:
    rid = new_id()
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM runs WHERE thread_id=?",
                       (tid,)).fetchone()[0]
    conn.execute(
        "INSERT INTO runs(id, thread_id, seq, prompt, provider, pinned, priority, parent, exclude,"
        " row, attachments, model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, tid, seq, prompt, provider, 1 if provider else 0, priority, parent,
         dumps(exclude or []), row, dumps(attachments or []), model, now()))
    return rid


def run(conn: sqlite3.Connection, rid: str) -> Optional[sqlite3.Row]:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM runs WHERE id LIKE ? ORDER BY created_at DESC",
                           (rid + "%",)).fetchone()
    return row


def runs_in(conn: sqlite3.Connection, states: tuple) -> List[sqlite3.Row]:
    marks = ",".join("?" * len(states))
    return conn.execute(f"SELECT * FROM runs WHERE state IN ({marks}) ORDER BY created_at",
                        states).fetchall()


def recent_runs(conn: sqlite3.Connection, limit: int = 20) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def thread_runs(conn: sqlite3.Connection, tid: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs WHERE thread_id=? ORDER BY seq", (tid,)).fetchall()


def update_run(conn: sqlite3.Connection, rid: str, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), rid))
    if "state" in fields:
        # every change of state is an event too, so anything following the run
        # (the web page's long poll, `eki follow`) hears about it at once
        attempt = conn.execute("SELECT attempt FROM runs WHERE id=?", (rid,)).fetchone()
        add_event(conn, rid, attempt[0] if attempt else 0, "state", {"state": fields["state"]})


def attachments(row: sqlite3.Row) -> List[str]:
    """The pictures sent with a run: absolute paths, [] when there are none."""
    try:
        value = json.loads(row["attachments"] or "[]")
    except (IndexError, KeyError, ValueError):
        return []
    return [str(p) for p in value] if isinstance(value, list) else []


def excluded(row: sqlite3.Row) -> List[str]:
    try:
        return list(json.loads(row["exclude"] or "[]"))
    except ValueError:
        return []


def cancel(conn: sqlite3.Connection, rid: str) -> str:
    """Ask for a run to stop. A queued run ends here; a running one is told."""
    with tx(conn):
        r = run(conn, rid)
        if r is None:
            return "unknown"
        if r["state"] == "queued":
            update_run(conn, r["id"], state="cancelled", cancel=1, ended_at=now())
            return "cancelled"
        if r["state"] in ("starting", "running"):
            update_run(conn, r["id"], cancel=1)
            return "stopping"
        return r["state"]


# ---- events ----------------------------------------------------------------------------

def add_event(conn: sqlite3.Connection, rid: str, attempt: int, kind: str,
              data: Dict[str, Any]) -> None:
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (rid, attempt, now(), kind, dumps(data)))


def events_after(conn: sqlite3.Connection, rid: str, after: int = 0) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id",
                        (rid, after)).fetchall()


def answer(conn: sqlite3.Connection, rid: str) -> str:
    """What a run said, from its last attempt that said anything."""
    rows = conn.execute("SELECT attempt, data FROM events WHERE run_id=? AND kind='text' ORDER BY id",
                        (rid,)).fetchall()
    if not rows:
        return ""
    by_attempt: Dict[int, List[str]] = {}
    for r in rows:
        by_attempt.setdefault(r["attempt"], []).append(json.loads(r["data"]).get("text", ""))
    # a resumed attempt carries on from where the last one stopped: keep them all
    return "".join("".join(parts) for _, parts in sorted(by_attempt.items()))


def last_picture(conn: sqlite3.Connection, tid: str, before_seq: int) -> Optional[str]:
    """The newest picture in a thread before a run: one a run drew (a `tool`
    event named image) or was given (a picture attachment), whichever came
    later. A run's attachments come before its events. Only a file still there."""
    from .routing.needs import is_picture
    for r in reversed([r for r in thread_runs(conn, tid) if r["seq"] < before_seq]):
        found: List[str] = [p for p in attachments(r) if is_picture(p)]
        for e in conn.execute("SELECT data FROM events WHERE run_id=? AND kind='tool' ORDER BY id",
                              (r["id"],)).fetchall():
            data = json.loads(e["data"] or "{}")
            if data.get("name") == "image" and data.get("path"):
                found.append(str(data["path"]))
        for p in reversed(found):
            if os.path.isfile(p):
                return p
    return None


def transcript(conn: sqlite3.Connection, tid: str, before_seq: int) -> List[Dict[str, str]]:
    """The thread so far as chat messages, for a provider joining it."""
    return transcript_since(conn, tid, 0, before_seq)


def transcript_since(conn: sqlite3.Connection, tid: str, after_seq: int,
                     before_seq: int) -> List[Dict[str, str]]:
    """The messages between two points of a thread: what a provider missed."""
    out: List[Dict[str, str]] = []
    for r in thread_runs(conn, tid):
        if r["seq"] <= after_seq:
            continue
        if r["seq"] >= before_seq:
            break
        if r["state"] == "handed_off":
            continue            # the same prompt comes again in the run it handed to
        marks = "".join(f"\n[picture: {p}]" for p in attachments(r))   # past pictures, as text
        out.append({"role": "user", "content": r["prompt"] + marks})
        said = answer(conn, r["id"])
        if said:
            out.append({"role": "assistant", "content": said})
    return out


# ---- cooldowns -------------------------------------------------------------------------

def cool(conn: sqlite3.Connection, provider: str, until: float, reason: str) -> None:
    conn.execute("INSERT INTO cooldowns(provider, until, reason) VALUES (?,?,?) ON CONFLICT(provider)"
                 " DO UPDATE SET until=excluded.until, reason=excluded.reason",
                 (provider, until, reason))


def cooling(conn: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    return {r["provider"]: r for r in
            conn.execute("SELECT * FROM cooldowns WHERE until>?", (now(),)).fetchall()}
