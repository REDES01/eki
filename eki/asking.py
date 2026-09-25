"""Turning a request into a run — the one way in, for the CLI and the web UI alike."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional, Tuple

from . import db, providers, store


def submit(conn: sqlite3.Connection, prompt: str, *, thread: Optional[str] = None,
           continue_last: bool = False, to: Optional[str] = None, cwd: Optional[str] = None,
           background: bool = False) -> Tuple[str, str]:
    """(thread id, run id). Raises KeyError/ValueError on a bad request."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("nothing to ask")
    if to and to not in providers.config():
        raise KeyError(f"no provider named {to!r}")
    if cwd:
        cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(cwd):
            raise ValueError(f"no folder {cwd}")
    with db.tx(conn):
        tid = None
        if thread:
            t = store.thread(conn, thread)
            if t is None:
                raise KeyError(f"no thread {thread}")
            tid = t["id"]
        elif continue_last:
            last = conn.execute("SELECT thread_id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
            tid = last[0] if last else None
        if tid is None:
            tid = store.create_thread(conn, prompt.splitlines()[0], cwd)
        elif cwd:
            conn.execute("UPDATE threads SET cwd=? WHERE id=?", (cwd, tid))
        rid = store.create_run(conn, tid, prompt, provider=to or None,
                               priority="background" if background else "now")
    return tid, rid
