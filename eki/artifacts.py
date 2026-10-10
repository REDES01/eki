"""What your chats made, newest first — the window's Artifacts page.

An artifact is a file a run wrote that is still on disk: a `tool` event with
a `path` (Claude Code's Write/Edit/MultiEdit/NotebookEdit, Codex's file
changes, a worker's answer-named files) or a picture eki drew (`gallery`'s
"image" events). Only three kinds count — pictures, pages and documents, by
extension. A relative path is read against the thread's folder.

Only your chats (`threads.by IS NULL`) count, and nothing of eki's own under
`~/.eki` except its pictures in `images/`. Each path shows once, at its
newest event.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import paths

KINDS: Dict[str, Set[str]] = {
    "picture": {"svg", "png", "jpg", "jpeg", "gif", "webp"},
    "page": {"html", "htm"},
    "document": {"md", "markdown", "pdf"},
}

#: how many events are read at a time while looking for files still on disk
_BATCH = 200

_SELECT = ("SELECT e.id, e.t, e.data, r.id AS run, r.thread_id, r.prompt, r.provider, "
           " t.title, t.cwd, t.project FROM events e JOIN runs r ON r.id = e.run_id "
           "JOIN threads t ON t.id = r.thread_id "
           "WHERE e.kind = 'tool' AND t.by IS NULL AND json_extract(e.data, '$.path') IS NOT NULL")


def kind_of(path: str, tool: Optional[str] = None) -> Optional[str]:
    """picture | page | document, or None for any other file."""
    if tool == "image":
        return "picture"
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return None


def _resolve(raw: str, cwd: Optional[str]) -> Optional[str]:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if not cwd:
            return None
        p = Path(cwd) / p
    return os.path.normpath(str(p))


def _eki_own(path: str) -> bool:
    """Under ~/.eki, but not one of its pictures in images/."""
    home = paths.home().resolve()
    try:
        rel = Path(path).resolve().relative_to(home)
    except ValueError:
        return False
    return not rel.parts or rel.parts[0] != "images"


def _matches(q: str, name: str, title: str, prompt: str) -> bool:
    return not q or any(q in (s or "").lower() for s in (name, title, prompt))


def listing(conn: sqlite3.Connection, kind: str = "all", q: str = "", limit: int = 60,
            before: Optional[float] = None) -> Dict[str, Any]:
    """Up to `limit` artifacts of `kind` (all | picture | page | document),
    newest first; `q` keeps those whose file name, thread title or prompt
    holds it; `before` (an event time) pages back past the ones shown.
    `more` says another page may exist."""
    if kind != "all" and kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}: all, picture, page or document")
    q = (q or "").strip().lower()
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    where, args = "", []                     # type: str, List[Any]
    if before is not None:
        where, args = " AND e.t < ?", [float(before)]
    while len(out) <= limit:
        rows = conn.execute(_SELECT + where + " ORDER BY e.t DESC, e.id DESC LIMIT ?",
                            (*args, _BATCH)).fetchall()
        for e in rows:
            item = _item(conn, e, kind, q, seen, before)
            if item:
                out.append(item)
                if len(out) > limit:
                    break
        if len(rows) < _BATCH or len(out) > limit:
            break
        last = rows[-1]                      # the next batch starts past the last one read
        where, args = " AND (e.t < ? OR (e.t = ? AND e.id < ?))", [last["t"], last["t"], last["id"]]
    return {"items": out[:limit], "more": len(out) > limit}


def _item(conn: sqlite3.Connection, e: sqlite3.Row, kind: str, q: str, seen: Set[str],
          before: Optional[float]) -> Optional[Dict[str, Any]]:
    data = json.loads(e["data"])
    raw = data.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    path = _resolve(raw, e["cwd"])
    if not path or path in seen:
        return None
    seen.add(path)                           # the newest event for a path decides, kept or not
    got = kind_of(path, data.get("name"))
    if not got or (kind != "all" and got != kind):
        return None
    name = os.path.basename(path)
    if not _matches(q, name, e["title"], e["prompt"]) or not os.path.isfile(path) or _eki_own(path):
        return None
    if before is not None and _newer(conn, path, before):
        return None                          # shown on an earlier page already
    return {"path": path, "name": name, "kind": got, "ext": os.path.splitext(name)[1].lower().lstrip("."),
            "run": e["run"], "thread": e["thread_id"], "thread_title": e["title"] or "",
            "project": e["project"], "created_at": e["t"], "prompt": e["prompt"],
            "provider": e["provider"]}


def _newer(conn: sqlite3.Connection, path: str, before: float) -> bool:
    """A newer event for the same file, so an earlier page already had it."""
    for e in conn.execute(_SELECT + " AND e.t >= ? AND instr(json_extract(e.data, '$.path'), ?) > 0",
                          (before, os.path.basename(path))):
        if _resolve(json.loads(e["data"])["path"], e["cwd"]) == path:
            return True
    return False
