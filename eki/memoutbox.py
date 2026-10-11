"""Memory, eki → client: the owner's durable facts, one small note each, in the outbox.

When you ask eki to remember something, correct it, or say what was decided in
a chat or a card, the words go to `~/.eki/sync/outbox/memory/<date>-<slug>.md`
for the client task to propose to Claude's memory. Only your own top-level runs
count; eki's own code work (goal and item threads) never does — its facts about
code stay in the repo. Nothing is held in memory: the cursor is the baseline
'memory-out' = {after: created_at of the last run looked at, seen: recent ids}.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import syncstate

NAME = "memory-out"
SEEN_MAX = 200
SLUG_MAX = 40
DESC_MAX = 100
WORDS_MAX = 2000

_REMEMBER = re.compile(r"\b(?:remember|don'?t forget|do not forget|note that|from now on|always|never)\b", re.I)
_DECISION = re.compile(r"\b(?:decided|i decided|we decided|let'?s go with|we'?ll use|we will use"
                       r"|the decision is)\b", re.I)


def folder() -> Path:
    p = syncstate.outbox() / "memory"
    p.mkdir(exist_ok=True)
    return p


def signal(conn: sqlite3.Connection, run: sqlite3.Row) -> Optional[str]:
    """'correction', 'remember', 'decision', or None for a plain message."""
    if conn.execute("SELECT 1 FROM journal WHERE kind='correction' AND run_id=? LIMIT 1",
                    (run["id"],)).fetchone():
        return "correction"
    words = run["prompt"] or ""
    if _REMEMBER.search(words):
        return "remember"
    if _DECISION.search(words):
        return "decision"
    return None


def _code_thread(conn: sqlite3.Connection, tid: str) -> bool:
    """A goal's or an item's thread: eki's own code work (as observe.check_correction tests)."""
    return conn.execute("SELECT 1 FROM goals WHERE thread_id=? UNION ALL "
                        "SELECT 1 FROM items WHERE thread_id=? LIMIT 1", (tid, tid)).fetchone() is not None


def _inside(cwd: Path, root: str) -> bool:
    try:
        cwd.relative_to(Path(root).expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False


def project_of(conn: sqlite3.Connection, cwd: Optional[str]) -> str:
    """The registered project whose folder holds `cwd` (the deepest one), else 'none'."""
    if not cwd:
        return "none"
    try:
        where = Path(cwd).expanduser().resolve()
    except OSError:
        return "none"
    best, depth = "none", -1
    for p in conn.execute("SELECT * FROM projects WHERE COALESCE(state, 'on') NOT IN ('deleting','deleted')"):
        for root in (p["path"], p["folder"] if "folder" in p.keys() else None):
            if root and _inside(where, root) and len(Path(root).parts) > depth:
                best, depth = p["name"], len(Path(root).parts)
    return best


def slug(words: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", words.lower()).strip("-")
    return s[:SLUG_MAX].rstrip("-") or "note"


def _free(base: Path, stem: str) -> Path:
    path, n = base / f"{stem}.md", 1
    while path.exists():
        n += 1
        path = base / f"{stem}-{n}.md"
    return path


def _one_line(text: str) -> str:
    return " ".join(text.split())


def write_note(conn: sqlite3.Connection, run: sqlite3.Row, sig: str) -> Path:
    words = (run["prompt"] or "").strip()[:WORDS_MAX]
    day = time.strftime("%Y-%m-%d", time.localtime(run["created_at"]))
    cwd = conn.execute("SELECT cwd FROM threads WHERE id=?", (run["thread_id"],)).fetchone()
    project = project_of(conn, cwd["cwd"] if cwd else None)
    desc = _one_line(words)[:DESC_MAX].replace('"', "'")
    path = _free(folder(), f"{day}-{slug(words)}")
    text = (f'---\ndescription: "{desc}"\nproject: {project}\nsignal: {sig}\n'
            f"run: {run['id']}\ndate: {day}\n---\n\n{words}\n")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


def _count() -> int:
    return sum(1 for p in folder().glob("*.md"))


def tick(conn: sqlite3.Connection) -> List[str]:
    cursor: Dict = syncstate.baseline(NAME)
    said: List[str] = []
    if "after" not in cursor:                       # first pass: start now, no backfill
        syncstate.set_baseline(NAME, {"after": time.time(), "seen": []})
        syncstate.record(NAME, count=_count())
        return said
    after = float(cursor.get("after") or 0)
    seen = [s for s in cursor.get("seen") or [] if isinstance(s, str)]
    known = set(seen)
    rows = conn.execute(
        "SELECT * FROM runs WHERE created_at>=? AND parent IS NULL"
        " AND (provider IS NULL OR provider!='command') ORDER BY created_at, id", (after,)).fetchall()
    for run in rows:
        if run["id"] in known:
            continue
        after = max(after, run["created_at"])
        seen.append(run["id"])
        known.add(run["id"])
        if _code_thread(conn, run["thread_id"]):
            continue
        sig = signal(conn, run)
        if sig is None:
            continue
        path = write_note(conn, run, sig)
        said.append(f"memory-out: {path.name} ({sig})")
    syncstate.set_baseline(NAME, {"after": after, "seen": seen[-SEEN_MAX:]})
    syncstate.record(NAME, count=_count())
    return said
