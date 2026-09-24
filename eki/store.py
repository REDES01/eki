# SPDX-License-Identifier: Apache-2.0
"""One store for every conversation, whichever backend answered.

The schema records which backend served each turn, so a thread can start on a
local model and continue on Claude Code without becoming two histories. That
mixing is the whole point: the router picks per turn, the conversation is one
thing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    backend         TEXT,
    reason          TEXT,
    meta            TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns (conversation_id, id);

-- a backend that keeps its own session state (Claude Code, Codex) maps its id
-- here, so a follow-up resumes that thread instead of starting a new one
CREATE TABLE IF NOT EXISTS backend_sessions (
    conversation_id TEXT NOT NULL,
    backend         TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, backend)
);
"""


class Store:
    def __init__(self, path: Path | str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(conversations)")}
        # pinned stays at the top; archived leaves the list but keeps its history
        if "pinned" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "archived" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        # who chose the title: '' (the first line, a placeholder), 'model', 'user'
        if "title_source" not in cols:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN title_source TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    # --- conversations ------------------------------------------------------

    def set_conversation(self, conversation_id: str, *, title: Optional[str] = None,
                         pinned: Optional[bool] = None,
                         archived: Optional[bool] = None) -> bool:
        fields, values = [], []
        if title is not None:
            # a title the user typed is theirs; the model never replaces it
            fields += ["title = ?", "title_source = 'user'"]
            values.append(title.strip()[:120])
        if pinned is not None:
            fields.append("pinned = ?"); values.append(int(pinned))
        if archived is not None:
            fields.append("archived = ?"); values.append(int(archived))
        if not fields:
            return False
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?",
                (*values, conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def suggest_title(self, conversation_id: str, title: str) -> bool:
        """A model's title — only over a placeholder or an older suggestion."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE conversations SET title = ?, title_source = 'model'"
                " WHERE id = ? AND title_source != 'user'",
                (title.strip()[:120], conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def untitled(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Threads still titled by their first line, that have an answer."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id FROM conversations c WHERE c.title_source = ''"
                " AND EXISTS (SELECT 1 FROM turns t WHERE t.conversation_id = c.id"
                "             AND t.role = 'assistant')"
                " ORDER BY c.updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def title_source(self, conversation_id: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT title_source FROM conversations WHERE id = ?",
                                     (conversation_id,)).fetchone()
        return row["title_source"] if row else ""

    def delete_conversation(self, conversation_id: str) -> bool:
        """Gone for good: the thread, its turns and its resumable sessions."""
        with self._lock:
            self._conn.execute("DELETE FROM turns WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute("DELETE FROM backend_sessions WHERE conversation_id = ?",
                               (conversation_id,))
            cur = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def new_conversation(self, title: str = "") -> str:
        cid = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at)"
                " VALUES (?,?,?,?)", (cid, title, now, now))
            self._conn.commit()
        return cid

    def latest_conversation(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return row["id"] if row else None

    def conversations(self, limit: int = 20, archived: bool = False) -> List[Dict[str, Any]]:
        """Pinned first, then newest; archived ones only when asked for."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.title, c.updated_at, c.pinned, c.archived,"
                "       (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.id) n"
                " FROM conversations c WHERE c.archived = ?"
                " ORDER BY c.pinned DESC, c.updated_at DESC, c.rowid DESC LIMIT ?", (int(archived), limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # --- turns --------------------------------------------------------------

    def add_turn(self, conversation_id: str, role: str, content: str,
                 backend: str = "", reason: str = "",
                 meta: Optional[Dict[str, Any]] = None) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO turns (conversation_id, role, content, backend, reason,"
                " meta, created_at) VALUES (?,?,?,?,?,?,?)",
                (conversation_id, role, content, backend, reason,
                 json.dumps(meta or {}), now))
            turn_id = int(cur.lastrowid)
            self._conn.execute(
                "UPDATE conversations SET updated_at = ?,"
                " title = CASE WHEN title = '' OR title IS NULL"
                "              THEN substr(?, 1, 60) ELSE title END"
                " WHERE id = ?", (now, content if role == "user" else "", conversation_id))
            self._conn.commit()
        return turn_id

    def turns(self, conversation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                # id included deliberately: a client needs a stable key per turn
                "SELECT id, role, content, backend, reason, meta, created_at FROM turns"
                " WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
        return [dict(r) for r in rows]

    def made(self, limit: int = 400) -> List[Dict[str, Any]]:
        """Answers that may hold something made — a fence or a picture — newest
        first, each with the thread it belongs to.

        Deliberately coarse. What counts as an artifact is decided in one
        place, by the reader that draws the chat; this only spares it the
        answers that are plainly nothing but words.

        `files` are what the run wrote into its folder that the gallery can
        show (`meta["made"]`, eki/files.py) — an agent's page lands in the
        repo, not in its answer — less any since deleted."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.id, t.conversation_id, c.title, c.archived, t.content,"
                "       t.backend, t.created_at, t.meta"
                " FROM turns t JOIN conversations c ON c.id = t.conversation_id"
                " WHERE t.role = 'assistant'"
                "   AND (t.content LIKE '%```%' OR t.content LIKE '%![%'"
                "        OR t.content LIKE '%.png%' OR t.content LIKE '%.jpg%'"
                "        OR t.meta LIKE '%\"made\"%')"
                " ORDER BY t.id DESC LIMIT ?", (max(1, min(int(limit), 2000)),)).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            try:
                made = (json.loads(row.pop("meta") or "{}") or {}).get("made") or []
            except (TypeError, ValueError, AttributeError):
                made = []
            row["files"] = [p for p in made if isinstance(p, str) and Path(p).is_file()]
            out.append(row)
        return out

    def search(self, text: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Conversations containing this text, newest first, with the hit."""
        like = f"%{text}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.title, c.updated_at, c.pinned, c.archived,"
                "       (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.id) n,"
                # the first matching line, so the result says why it matched
                "       (SELECT substr(t.content, 1, 160) FROM turns t"
                "         WHERE t.conversation_id = c.id AND t.content LIKE ?"
                "         ORDER BY t.id LIMIT 1) hit"
                " FROM conversations c"
                " WHERE EXISTS (SELECT 1 FROM turns t WHERE t.conversation_id = c.id"
                "               AND t.content LIKE ?)"
                "    OR c.title LIKE ?"
                " ORDER BY c.updated_at DESC LIMIT ?",
                (like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]

    def cost(self, conversation_id: str) -> Dict[str, Any]:
        """What this thread spent, split by who answered.

        Tokens are only counted where the backend reported them — a local
        model and a CLI under a subscription both cost nothing per token, so
        the honest answer is turns-by-backend, not an invented dollar figure.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT backend, meta FROM turns"
                " WHERE conversation_id = ? AND role = 'assistant'",
                (conversation_id,)).fetchall()
        by: Dict[str, Dict[str, int]] = {}
        for row in rows:
            key = row["backend"] or "?"
            entry = by.setdefault(key, {"turns": 0, "input_tokens": 0,
                                        "output_tokens": 0})
            entry["turns"] += 1
            try:
                usage = (json.loads(row["meta"] or "{}") or {}).get("usage") or {}
            except ValueError:
                usage = {}
            entry["input_tokens"] += int(usage.get("input_tokens") or 0)
            entry["output_tokens"] += int(usage.get("output_tokens") or 0)
        return {"conversation": conversation_id, "by_backend": by,
                "turns": sum(e["turns"] for e in by.values())}

    # --- backend session mapping -------------------------------------------

    def set_session(self, conversation_id: str, backend: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO backend_sessions (conversation_id, backend, session_id,"
                " updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(conversation_id, backend) DO UPDATE SET"
                " session_id = excluded.session_id, updated_at = excluded.updated_at",
                (conversation_id, backend, session_id, int(time.time())))
            self._conn.commit()

    def session(self, conversation_id: str, backend: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM backend_sessions"
                " WHERE conversation_id = ? AND backend = ?",
                (conversation_id, backend)).fetchone()
        return row["session_id"] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
