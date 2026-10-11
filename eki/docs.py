"""A local text file opened in the window's side panel: read, edited, and asked open from the CLI.

The policy is the whole point: the window reads and writes only the person's own files under
their home folder (real path, so a symlink can't lead out), never eki's internals, never a
folder or file that holds credentials, and edits only .md, .markdown and .txt. A file a run
pointed at (`files.known`) or a digest page stays readable read-only wherever it is, as before.

A save is atomic (temp file in the same folder, fsync, rename, the file's mode kept) and only
over the text it was edited from: a moved hash answers a conflict and writes nothing. Saves and
`eki open` requests are events rows (run_id 'window'), so a restart loses neither.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

from . import files, paths

MAX = 2 * 1024 * 1024
EDITABLE = (".md", ".markdown", ".txt")
SECRET_DIRS = {"Library", ".ssh", ".gnupg", ".aws", ".azure", ".config", ".kube", ".docker", ".codex",
               ".claude", ".password-store", ".Trash"}
SECRET_FILES = {".netrc", ".npmrc", ".pypirc", ".git-credentials", ".claude.json"}
WINDOW = "window"


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _secret(real: str, uhome: str) -> bool:
    parts = os.path.relpath(real, uhome).split(os.sep)
    return any(p in SECRET_DIRS for p in parts) or os.path.basename(real) in SECRET_FILES


def policy(conn: sqlite3.Connection, path: str) -> Tuple[str, Optional[str]]:
    """(real path, why it's read-only or None when editable); ValueError(reason) when not readable."""
    if not path or not str(path).strip():
        raise ValueError("no path given")
    given = os.path.abspath(os.path.expanduser(str(path).strip()))
    real = os.path.realpath(given)
    uhome = os.path.realpath(str(paths.user_home()))
    ehome = os.path.realpath(str(paths.home()))
    if _under(real, uhome) and _secret(real, uhome):
        raise ValueError("that folder or file can hold credentials; the window never opens it")
    pointed = files.known(conn, given) or files.known(conn, real) or files.digest_page(given)
    if _under(real, ehome):
        if pointed:
            return real, "eki's own file — read-only"
        raise ValueError("eki's own internal files can't be opened")
    if not _under(real, uhome):
        if pointed:
            return real, "outside your home folder — read-only"
        if _under(given, uhome) or _under(given, os.path.abspath(str(paths.user_home()))):
            raise ValueError("a link that leads outside your home folder can't be opened")
        raise ValueError("only files under your home folder can be opened")
    if not real.lower().endswith(EDITABLE):
        return real, "only .md, .markdown and .txt files can be edited"
    return real, None


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(data: bytes) -> str:
    if b"\0" in data:
        raise ValueError("not a text file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not a text file") from None


def _load(real: str) -> bytes:
    if not os.path.isfile(real):
        raise KeyError(f"no file at {real}")
    if os.path.getsize(real) > MAX:
        raise ValueError("too large to open (over 2 MB)")
    with open(real, "rb") as f:
        return f.read()


def writer(conn: sqlite3.Connection, given: str, real: str, mtime: float) -> Optional[Dict[str, Any]]:
    """Who last wrote the file, when eki knows: the newest tool event naming it or window save of it,
    counted only when it's within 5 s of (or after) the file's last change."""
    run = conn.execute("SELECT run_id, t FROM events WHERE kind='tool' AND json_extract(data, '$.path') IN (?, ?) "
                       "ORDER BY t DESC, id DESC LIMIT 1", (given, real)).fetchone()
    you = conn.execute("SELECT t FROM events WHERE kind='doc_save' AND run_id=? AND json_extract(data, '$.path') = ? "
                       "ORDER BY t DESC, id DESC LIMIT 1", (WINDOW, real)).fetchone()
    found = []
    if run is not None:
        found.append({"by": "run", "run": run[0], "t": float(run[1])})
    if you is not None:
        found.append({"by": "you", "run": None, "t": float(you[0])})
    found = [w for w in found if w["t"] >= mtime - 5]
    return max(found, key=lambda w: w["t"]) if found else None


def read(conn: sqlite3.Connection, path: str) -> Dict[str, Any]:
    real, reason = policy(conn, path)
    data = _load(real)
    text = _text(data)
    mtime = os.path.getmtime(real)
    given = os.path.abspath(os.path.expanduser(str(path).strip()))
    return {"path": real, "name": os.path.basename(real), "text": text, "mtime": mtime, "hash": _hash(data),
            "size": len(data), "editable": reason is None, "reason": reason,
            "writer": writer(conn, given, real, mtime)}


def _atomic(real: str, data: bytes) -> None:
    folder = os.path.dirname(real)
    mode = os.stat(real).st_mode & 0o7777 if os.path.exists(real) else 0o644
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(real) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write(conn: sqlite3.Connection, path: str, text: str, base_hash: str) -> Dict[str, Any]:
    """Saved, or a conflict carrying the disk's text when the file moved since it was loaded."""
    real, reason = policy(conn, path)
    if reason is not None:
        raise ValueError(reason)
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    data = text.encode("utf-8")
    if len(data) > MAX:
        raise ValueError("too large to save (over 2 MB)")
    if not os.path.isdir(os.path.dirname(real)):
        raise ValueError(f"no folder {os.path.dirname(real)}")
    if os.path.exists(real):
        disk = _load(real)
        if _hash(disk) != base_hash:
            return {"conflict": True, "text": _text(disk), "mtime": os.path.getmtime(real), "hash": _hash(disk)}
    _atomic(real, data)
    new = _hash(data)
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (WINDOW, 0, time.time(), "doc_save", json.dumps({"path": real, "hash": new})))
    conn.commit()
    return {"saved": True, "mtime": os.path.getmtime(real), "hash": new}


def request_open(conn: sqlite3.Connection, path: str) -> int:
    """Ask the window to open a file (`eki open`); it must pass what a read would."""
    real = read(conn, path)["path"]
    cur = conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                       (WINDOW, 0, time.time(), "doc_open", json.dumps({"path": real})))
    conn.commit()
    return int(cur.lastrowid)


def opens(conn: sqlite3.Connection, after: Optional[int]) -> Dict[str, Any]:
    """Open requests after `after`; with None, none and the newest id, so a page starts from now."""
    if after is None:
        row = conn.execute("SELECT MAX(id) FROM events WHERE kind='doc_open' AND run_id=?", (WINDOW,)).fetchone()
        return {"opens": [], "last": int(row[0] or 0)}
    rows = conn.execute("SELECT id, t, json_extract(data, '$.path') FROM events WHERE kind='doc_open' "
                        "AND run_id=? AND id > ? ORDER BY id", (WINDOW, int(after))).fetchall()
    got = [{"id": int(r[0]), "path": r[2], "t": float(r[1])} for r in rows]
    return {"opens": got, "last": got[-1]["id"] if got else int(after)}
