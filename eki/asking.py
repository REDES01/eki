"""Turning a request into a run — the one way in, for the CLI and the web UI alike."""
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional, Tuple

from . import db, projectchats, providers, store
from .attachments import intake


def submit(conn: sqlite3.Connection, prompt: str, *, project: Optional[str] = None,
           thread: Optional[str] = None,
           continue_last: bool = False, to: Optional[str] = None, cwd: Optional[str] = None,
           background: bool = False,
           attachments: Optional[List[str]] = None) -> Tuple[str, str]:
    """(thread id, run id). Raises KeyError/ValueError on a bad request.

    A new thread in a `project` runs in the project's folder; a thread that has
    a project keeps that folder for good."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("nothing to ask")
    if to and to not in providers.config():
        raise KeyError(f"no provider named {to!r}")
    if cwd:
        cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(cwd):
            raise ValueError(f"no folder {cwd}")
    files = []
    for path in attachments or []:
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.isfile(path):
            raise ValueError(f"no file {path}")
        files.append(intake(path))  # a HEIC or a huge picture gets a safe copy
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
        if tid is None and project:
            folder = _project_folder(conn, project)
            if cwd and projectchats.norm(cwd) != folder:
                raise ValueError(f"this chat lives in {_name(conn, project)} ({folder})")
            tid = store.create_thread(conn, prompt.splitlines()[0], folder)
            conn.execute("UPDATE threads SET project=? WHERE id=?", (project, tid))
        elif tid is None:
            tid = store.create_thread(conn, prompt.splitlines()[0], cwd)
        elif _keeps_folder(conn, tid, cwd):
            pass
        elif cwd:
            conn.execute("UPDATE threads SET cwd=? WHERE id=?", (cwd, tid))
        rid = store.create_run(conn, tid, prompt, provider=to or None,
                               priority="background" if background else "now",
                               attachments=files)
    return tid, rid


def _name(conn: sqlite3.Connection, pid: str) -> str:
    if pid == projectchats.SELF:
        return "eki"
    row = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError(f"no project {pid}")
    return row["name"]


def _project_folder(conn: sqlite3.Connection, pid: str) -> str:
    """The folder a new chat in this project runs in, or why there isn't one."""
    name = _name(conn, pid)
    folder = projectchats.folder_of(conn, pid)
    if not folder or not os.path.isdir(folder):
        raise ValueError(f"{name} has no folder yet: set one on its page or "
                         f"eki project {name} --folder <path>")
    return folder


def _keeps_folder(conn: sqlite3.Connection, tid: str, cwd: Optional[str]) -> bool:
    """True when the thread lives in a project: its folder stays; another cwd is refused."""
    t = conn.execute("SELECT cwd, project FROM threads WHERE id=?", (tid,)).fetchone()
    if not t["project"]:
        return False
    folder = t["cwd"] or projectchats.folder_of(conn, t["project"]) or ""
    if cwd and projectchats.norm(cwd) != projectchats.norm(folder):
        raise ValueError(f"this chat lives in {_name(conn, t['project'])} ({folder})")
    return True
