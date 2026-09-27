"""The project-only steps of self-work (eki/selfwork.py) for a goal with
`project` set: its worktree, its gate 1 and when it counts as applied.

eki only adds worktrees and `eki/<item>` branches to a project. It never
checks out, commits to, fetches into, resets or pushes the project's own
branches: an item is `applied` once the person merged it into the branch.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import projects, workspace

#: what a project item says when there is nothing to judge it with
NOT_JUDGED = "no check configured — not judged"


def project_of(conn: sqlite3.Connection, goal: Optional[sqlite3.Row]) -> Optional[sqlite3.Row]:
    """The goal's project row, or None for eki itself."""
    if goal is None or "project" not in goal.keys() or not goal["project"]:
        return None
    got = projects.get(conn, goal["project"])
    if got is None:
        raise KeyError(f"no project {goal['project']}")
    return got


def worktree(project: sqlite3.Row, iid: str) -> Tuple[Path, str, str]:
    """(worktree, base, branch) for item `iid`: `eki/<iid>` from the tip of the project's branch,
    its .venv / node_modules linked and kept out of git."""
    base = projects.base(project)
    branch = f"eki/{iid}"
    wt = workspace.add(project["path"], iid, base=base, branch=branch)
    projects.link_deps(project, wt)
    return wt, base, branch


def is_ancestor(where: str | Path, commit: str, head: str) -> bool:
    """Is `commit` in `head`'s history, in the repo at `where`? Judged by what git prints."""
    if not commit or not head:
        return False
    base = workspace.git(where, "merge-base", commit, head, check=False)
    return bool(base) and base == workspace.git(where, "rev-parse", "--verify", f"{commit}^{{commit}}",
                                                 check=False)


def merged(project: sqlite3.Row, commit: str) -> bool:
    """Has the person merged `commit` into the project's branch?"""
    tip = workspace.git(project["path"], "rev-parse", "--verify", "-q",
                        f"refs/heads/{project['branch']}^{{commit}}", check=False)
    return is_ancestor(project["path"], commit, tip)


def split(conn: sqlite3.Connection, items: List[sqlite3.Row]) -> Tuple[List[sqlite3.Row], List[tuple]]:
    """(eki's own items, [(item, project)]) — an item whose project is gone is left out."""
    own, theirs = [], []
    goals = {}
    for it in items:
        if it["goal_id"] not in goals:
            g = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
            try:
                goals[it["goal_id"]] = (project_of(conn, g),)
            except KeyError:
                goals[it["goal_id"]] = None
        got = goals[it["goal_id"]]
        if got is None:
            continue
        if got[0] is None:
            own.append(it)
        else:
            theirs.append((it, got[0]))
    return own, theirs
