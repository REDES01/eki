"""What each change that landed on the head was for (docs/self-build.md, "The queue").

A resolve run sees what the head did to the conflicted files; `intents` says
why: for every item that landed between the item's base and the head and
touched those files, its title, goal, spec and builder's summary — or, for a
person's own commit, its subject and body. Plain text, bounded, ready to go
into a prompt. Read from git and the items table only; nothing is stored.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .workspace import WorkspaceError, git

#: lines of an item's goal shown
GOAL_LINES = 3
#: separators in the log format: between fields, between commits (not \x1e/\x1f,
#: which str.strip takes for whitespace)
_F, _R = "\x02", "\x03"


def intents(conn: sqlite3.Connection, it: sqlite3.Row, head: str, files: List[str], *,
            limit: int = 6, trim: int = 600) -> str:
    """One block per change on `head` since `it`'s base that touched `files`, newest first."""
    commits = _commits(it, head, files)
    entries: List[str] = []
    seen = set()
    for sha, subject, body in commits:
        rows = conn.execute("SELECT * FROM items WHERE rebased=? OR commit_sha=? "
                            "ORDER BY updated_at DESC", (sha, sha)).fetchall()
        if any(r["id"] == it["id"] for r in rows):
            continue                                    # the item's own commit is no other side
        made = rows[0] if rows else None
        key = made["id"] if made is not None else sha
        if key in seen:
            continue
        seen.add(key)
        entries.append(_item(conn, made, trim) if made is not None else _person(sha, subject, body, trim))
    if not entries:
        return ""
    shown = entries[:limit]
    if len(entries) > limit:
        shown.append(f"({len(entries) - limit} more not shown)")
    return "\n\n".join(shown)


def _commits(it: sqlite3.Row, head: str, files: List[str]) -> List[tuple]:
    """(sha, subject, body) of the non-merge commits in base..head touching `files`; [] when git fails."""
    if not files or not it["worktree"]:
        return []
    since = it["base"] or head
    try:
        out = git(it["worktree"], "log", "--no-merges", "--format=%H%x02%s%x02%b%x03",
                  f"{since}..{head}", "--", *files)
    except (WorkspaceError, OSError):
        return []
    got = []
    for rec in out.split(_R):
        parts = rec.strip("\n").split(_F)
        if len(parts) == 3 and parts[0].strip():
            got.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return got


def _item(conn: sqlite3.Connection, made: sqlite3.Row, trim: int) -> str:
    goal = conn.execute("SELECT text FROM goals WHERE id=?", (made["goal_id"],)).fetchone()
    lines = ((goal["text"] if goal else "") or "").strip().splitlines()[:GOAL_LINES]
    return "\n".join([
        f"### {made['title']}",
        f"(eki item {made['id']})",
        "Goal: " + ("\n".join(lines) or "(none)"),
        "Spec: " + _cut(made["spec"], trim, "(none)"),
        "Builder's summary: " + _cut(made["summary"], trim, "(nothing said)"),
    ])


def _person(sha: str, subject: str, body: str, trim: int) -> str:
    return "\n".join([
        f"### {subject or sha[:12]}",
        f"(commit {sha[:12]}: a person's own commit, not an eki item)",
        "Message: " + _cut(body, trim, "(no body)"),
    ])


def _cut(text: Optional[str], trim: int, empty: str) -> str:
    text = (text or "").strip()
    if not text:
        return empty
    return text if len(text) <= trim else text[:trim].rstrip() + "…"
