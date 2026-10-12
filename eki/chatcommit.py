"""A project's history covers its chats: each chat turn's changes, committed.

A chat in a project runs in the person's own folder and edits it directly.
After each turn that changed files there, the change is recorded on the
project's branch as `eki: chat — <the request's first line>`, so it is in
the history, can be undone from the window, and goal items rebase onto it
instead of waiting on local changes. Files the person changed by hand
since are recorded first, on their own, as `eki: your edits`.

Only the project's own folder, on its branch, with the project's
`chat_history` setting on; never a worktree, never eki itself, never a
chore. Recording never resets, forces or checks out: it adds a commit or
does nothing. A failure (index.lock held by a landing) is a note on the
run; the changes are picked up by the next turn.
"""
from __future__ import annotations

import logging
import sqlite3
import subprocess
from typing import Optional, Tuple

from . import db, projectchats, projects, store, workspace

log = logging.getLogger("eki.chatcommit")

HAND = "eki: your edits"
CHAT = "eki: chat — "
#: the request's first line in a commit message is cut to this many characters
TITLE = 72
#: who commits in a repo with no identity of its own
FALLBACK = ["-c", "user.name=eki", "-c", "user.email=eki@localhost"]


class ChatCommitError(RuntimeError):
    pass


def _git(folder: str, *args: str, ident: Tuple[str, ...] = ()) -> str:
    out = subprocess.run(["git", "-c", "commit.gpgsign=false", *ident, "-C", folder, *args],
                         capture_output=True, text=True, env=workspace._env[0])
    if out.returncode != 0:
        raise ChatCommitError(f"git {' '.join(args[:1])}: {(out.stderr or out.stdout).strip()}")
    return out.stdout


def _ident(folder: str) -> Tuple[str, ...]:
    """Nothing when the repo has an identity; else eki's, through -c."""
    def have(key: str) -> bool:
        try:
            return bool(_git(folder, "config", "--get", key).strip())
        except ChatCommitError:
            return False
    return () if have("user.name") and have("user.email") else tuple(FALLBACK)


def changed(folder: str) -> bool:
    """Anything to record: tracked changes, deletions, untracked files not ignored."""
    return bool(_git(folder, "status", "--porcelain", "--untracked-files=all").strip())


def record(folder: str, message: str) -> Optional[str]:
    """Stage every change in `folder` (never an ignored file) and commit it with `message`.
    The new commit's sha, or None when nothing changed. Raises ChatCommitError on a git failure."""
    if not changed(folder):
        return None
    _git(folder, "add", "-A", "--", ".")          # -A respects .gitignore; ignored files stay out
    if not _git(folder, "diff", "--cached", "--name-only").strip():
        return None
    _git(folder, "commit", "-q", "--no-verify", "-m", message, ident=_ident(folder))
    return _git(folder, "rev-parse", "HEAD").strip()


def title(prompt: Optional[str]) -> str:
    """The prompt's first non-empty line, whitespace collapsed, cut to TITLE characters."""
    for line in (prompt or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= TITLE else line[:TITLE - 1] + "…"
    return "(no request)"


def target(conn: sqlite3.Connection, run_row: sqlite3.Row) -> Optional[Tuple[str, sqlite3.Row]]:
    """(folder, project row) whose changes this run records, or None."""
    if run_row["row"] == "chore":
        return None
    thread = store.thread(conn, run_row["thread_id"])
    if thread is None or not thread["cwd"]:
        return None
    pid = thread["project"] if "project" in thread.keys() else None
    if not pid or pid == projectchats.SELF:
        return None
    project = projects.get(conn, pid)
    if project is None or not projects.chat_history(project) or not project["branch"]:
        return None
    try:
        folder = projectchats.folder_of(conn, pid)
    except KeyError:
        return None
    if not folder or projectchats.norm(thread["cwd"]) != folder:
        return None
    try:
        top = _git(folder, "rev-parse", "--show-toplevel").strip()
        head = _git(folder, "symbolic-ref", "-q", "HEAD").strip()
    except (ChatCommitError, OSError):
        return None
    if projectchats.norm(top) != folder or head != f"refs/heads/{project['branch']}":
        return None
    return folder, project


def _note(conn: sqlite3.Connection, run_row: sqlite3.Row, why: str) -> None:
    try:
        with db.tx(conn):
            store.add_event(conn, run_row["id"], run_row["attempt"], "note",
                            {"text": f"chat history: not recorded — {why}"[:500]})
    except Exception as e:                      # noqa: BLE001 — never raises
        log.warning("chat history note for %s: %s", run_row["id"], e)


def _commit(conn: sqlite3.Connection, run_row: sqlite3.Row, message: str, name: str,
            kind: str) -> Optional[str]:
    try:
        got = target(conn, run_row)
        if got is None:
            return None
        folder = got[0]
        sha = record(folder, message)
        if sha is None:
            return None
        with db.tx(conn):
            store.add_event(conn, run_row["id"], run_row["attempt"], "commit",
                            {"sha": sha, "title": name, "kind": kind, "folder": folder})
        return sha
    except Exception as e:                      # noqa: BLE001 — a run never fails for its history
        log.warning("chat history for %s: %s", run_row["id"], e)
        _note(conn, run_row, " ".join(str(e).split()) or type(e).__name__)
        return None


def before_turn(conn: sqlite3.Connection, run_row: sqlite3.Row) -> Optional[str]:
    """On the run's first attempt: what the person changed by hand since, as `eki: your edits`."""
    try:
        if run_row["attempt"] != 1:
            return None
    except Exception:                           # noqa: BLE001 — never raises
        return None
    return _commit(conn, run_row, HAND, "your edits", "hand")


def after_turn(conn: sqlite3.Connection, run_row: sqlite3.Row) -> Optional[str]:
    """What the turn changed, as `eki: chat — <the request's first line>`."""
    try:
        name = title(run_row["prompt"])
    except Exception:                           # noqa: BLE001 — never raises
        return None
    return _commit(conn, run_row, CHAT + name, name, "chat")
