"""A project's chat history in the window: go back to before a chat turn, and the
switch that turns recording chats on or off (eki/chatcommit.py, eki/chatundo.py)."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Tuple

from . import chatundo, projects


def back(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, str]:
    sha = str(body.get("sha") or "").strip()
    if not sha:
        raise ValueError("which commit? send {\"sha\": ...}")
    return chatundo.back_to_before(conn, m.group(1), sha)


def chat_history(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, bool]:
    if not isinstance(body.get("on"), bool):
        raise ValueError("send {\"on\": true} or {\"on\": false}")
    pid = m.group(1)
    projects.set_chat_history(conn, pid, body["on"])
    row = projects.get(conn, pid)
    if row is None:
        raise KeyError(f"no project {pid}")
    return {"chat_history": projects.chat_history(row)}


ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/runs/([\w-]+)/back", back),
    ("POST", r"/api/projects/([^/]+)/chat-history", chat_history),
]
