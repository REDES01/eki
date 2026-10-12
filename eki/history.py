"""A project's history in plain words: the changes that landed, and going back.

Git is eki's engine, not the person's: they see "5 changes landed today" and
"go back to before <change>", never branches or commits. Going back is always
a new change on top (a revert of the change and everything after it); no
branch is ever reset, rewritten or force-moved.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import workspace
from .workspace import WorkspaceError, git

#: the subject of the first commit eki gives a folder (eki/gitstart.py)
FIRST = "the folder as it was"
CHAT_ONLY = {"today": 0, "line": "No history kept: chat only", "changes": []}


def _repo(folder: str | Path) -> Optional[str]:
    """The folder when it is the top of a git repo with a commit, else None."""
    top = workspace.repo_of(folder)
    if top is None or os.path.realpath(top) != os.path.realpath(str(folder)):
        return None
    if not git(top, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        return None
    return top


def _midnight() -> int:
    return int(datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp())


def line(today: int) -> str:
    if today == 0:
        return "No changes today"
    return f"{today} change{'' if today == 1 else 's'} landed today"


def _titles(conn: sqlite3.Connection, shas: List[str]) -> Dict[str, str]:
    """sha -> the title of the item whose work it is."""
    if not shas:
        return {}
    marks = ",".join("?" * len(shas))
    got: Dict[str, str] = {}
    for r in conn.execute(f"SELECT title, commit_sha, rebased FROM items "
                          f"WHERE commit_sha IN ({marks}) OR rebased IN ({marks}) ORDER BY created_at",
                          (*shas, *shas)):
        for sha in (r["commit_sha"], r["rebased"]):
            if sha in shas:
                got[sha] = r["title"]
    return got


def _log(conn: sqlite3.Connection, top: str, *revs: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    args = ["log", "--format=%H%x1f%ct%x1f%s", *([f"-n{limit}"] if limit else []), *revs]
    rows = [r.split("\x1f", 2) for r in git(top, *args).splitlines() if r]
    titles = _titles(conn, [r[0] for r in rows])
    out = []
    for sha, when, subject in rows:
        if sha in titles:
            out.append({"id": sha, "title": titles[sha], "when": int(when), "by": "eki"})
        else:
            out.append({"id": sha, "title": subject, "when": int(when), "by": "eki" if subject == FIRST else "you"})
    return out


def changes(conn: sqlite3.Connection, folder: str | Path, *, limit: int = 20) -> Dict[str, Any]:
    """How many changes landed today, in words, and the latest `limit`, newest first."""
    top = _repo(folder) if folder else None
    if top is None:
        return dict(CHAT_ONLY, changes=[])
    today = int(git(top, "rev-list", "--count", f"--since={_midnight()}", "HEAD") or 0)
    return {"today": today, "line": line(today), "changes": _log(conn, top, "HEAD", limit=limit)}


def _check(conn: sqlite3.Connection, top: str, change: str) -> Dict[str, Any]:
    """The change as `changes` shows it, or a plain ValueError why it can't be gone back from."""
    sha = git(top, "rev-parse", "--verify", "-q", f"{change}^{{commit}}", check=False)
    if not sha or workspace._run(top, ("merge-base", "--is-ancestor", sha, "HEAD")).returncode != 0:
        raise ValueError("that change isn't in this project's history")
    if not git(top, "rev-parse", "--verify", "-q", f"{sha}^", check=False):
        raise ValueError("that is the first change: nothing before it to go back to")
    if git(top, "rev-list", "--merges", f"{sha}^..HEAD"):
        raise ValueError("the changes since then include a merge: eki can't undo them as one")
    touched = set(git(top, "diff", "--name-only", f"{sha}^", "HEAD").splitlines())
    dirty = sorted(set(git(top, "diff", "--name-only", "HEAD").splitlines()) & touched)
    if dirty:
        raise ValueError(f"you have unsaved edits in {', '.join(dirty)}: save or discard them first")
    return _log(conn, top, "-n1", sha)[0]


def go_back(conn: sqlite3.Connection, folder: str | Path, change: str) -> Dict[str, Any]:
    """Undo `change` and every change after it as one new change on top."""
    top = _repo(folder)
    if top is None:
        raise ValueError("this project keeps no history: there is nothing to go back to")
    gone = _check(conn, top, change)
    title = f"Go back to before {gone['title']}"
    try:
        git(top, "revert", "--no-commit", f"{gone['id']}^..HEAD")
        if workspace._run(top, ("diff", "--cached", "--quiet")).returncode == 0:
            raise ValueError("nothing changed since then: there is nothing to undo")
        git(top, "commit", "-q", "-m", title)
    except (WorkspaceError, ValueError) as e:
        git(top, "revert", "--abort", check=False)
        git(top, "reset", "-q", check=False)
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"eki couldn't go back cleanly, so nothing changed: {e}") from e
    git(top, "revert", "--quit", check=False)
    return {"id": git(top, "rev-parse", "HEAD"), "title": title}
