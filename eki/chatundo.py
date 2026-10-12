"""Go back to before a chat turn: one new commit that puts the folder as it was.

A chat turn's change is a commit on the project's branch (eki/chatcommit.py),
named by a `commit` event on its run. Going back to before it never rewrites
history and never resets, forces or cleans: whatever is changed now is first
recorded as `eki: your edits`, then the tree of the commit's parent is read
into the folder and committed as `eki: back to before — <title>`. The old
commits stay in the history, so going back is itself undone the same way.
Ignored files (the media pool) are never touched.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, Optional

from . import chatcommit, db, projectchats, projects, store

BACK = "eki: back to before — "
#: git's empty tree: what a first commit's parent is
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _event(conn: sqlite3.Connection, run_id: str, sha: str) -> Dict[str, str]:
    for e in store.events_after(conn, run_id):
        if e["kind"] == "commit":
            data = json.loads(e["data"])
            if sha and data.get("sha") == sha:
                return data
    raise KeyError(f"no commit {sha} on run {run_id}")


def _git(folder: str, *args: str) -> str:
    try:
        return chatcommit._git(folder, *args)
    except (chatcommit.ChatCommitError, OSError) as e:
        raise ValueError(str(e)) from e


def _check(folder: str, branch: Optional[str], sha: str) -> None:
    """The folder still has the project's branch checked out, and `sha` is in its history."""
    if not branch:
        raise ValueError("the project has no branch to go back on")
    try:
        top = projectchats.norm(chatcommit._git(folder, "rev-parse", "--show-toplevel").strip())
        head = chatcommit._git(folder, "symbolic-ref", "-q", "HEAD").strip()
    except (chatcommit.ChatCommitError, OSError):
        raise ValueError(f"{folder} is no longer a git folder on a branch") from None
    if top != projectchats.norm(folder) or head != f"refs/heads/{branch}":
        raise ValueError(f"{folder} no longer has the project's branch {branch} checked out")
    try:
        chatcommit._git(folder, "merge-base", "--is-ancestor", sha, f"refs/heads/{branch}")
    except (chatcommit.ChatCommitError, OSError):
        raise ValueError(f"{sha[:12]} is not in the history of {branch}") from None


def back_to_before(conn: sqlite3.Connection, run_id: str, sha: str) -> Dict[str, str]:
    """One new commit whose tree is that of `sha`'s parent; `{"sha", "title"}` of it."""
    run = store.run(conn, run_id)
    if run is None:
        raise KeyError(f"no run {run_id}")
    ev = _event(conn, run["id"], sha)
    folder = ev.get("folder") or ""
    thread = store.thread(conn, run["thread_id"])
    pid = thread["project"] if thread is not None else None
    project = projects.get(conn, pid) if pid else None
    if project is None:
        raise ValueError("this chat is not in a project any more")
    _check(folder, project["branch"], sha)
    hand = chatcommit.record(folder, chatcommit.HAND) if chatcommit.changed(folder) else None
    if hand:
        with db.tx(conn):
            store.add_event(conn, run["id"], run["attempt"], "commit",
                            {"sha": hand, "title": "your edits", "kind": "hand", "folder": folder})
    parents = _git(folder, "rev-list", "--parents", "-n", "1", sha).split()
    before = f"{parents[1]}^{{tree}}" if len(parents) > 1 else EMPTY_TREE
    if _git(folder, "rev-parse", before).strip() == _git(folder, "rev-parse", "HEAD^{tree}").strip():
        raise ValueError("the folder is already as it was before this")
    message = BACK + str(ev.get("title") or "")
    _git(folder, "read-tree", "-u", "-m", "HEAD", before)     # tracked files only; ignored ones stay
    try:
        chatcommit._git(folder, "commit", "-q", "--no-verify", "-m", message,
                        ident=chatcommit._ident(folder))
    except chatcommit.ChatCommitError as e:
        raise ValueError(f"{e} — the earlier files are in the folder, staged, not committed") from e
    new = _git(folder, "rev-parse", "HEAD").strip()
    with db.tx(conn):
        store.add_event(conn, run["id"], run["attempt"], "commit",
                        {"sha": new, "title": ev.get("title") or "", "kind": "back", "folder": folder})
    return {"sha": new, "title": message}
