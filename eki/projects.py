"""A person's own git folders eki works on (standing goals, docs/design.md).

A project is one row per path. eki only ever adds worktrees and `eki/*`
branches to it: it never checks out, commits to, fetches into, resets or
pushes the project's branches — the person merges `eki/<item>` when they
want it. A goal with `project` NULL is eki itself (the integration repo).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from . import builds, db, integration, store, workspace

#: untracked folders a worktree needs to run, linked from the project folder
DEPS = (".venv", "node_modules")


def add(conn: sqlite3.Connection, path: str | Path, *, check: Optional[str] = None,
        branch: Optional[str] = None, name: Optional[str] = None) -> str:
    """Record the git folder at `path`; the same path again is the same project."""
    where = Path(path).expanduser().resolve()
    if not where.is_dir() or workspace.repo_of(where) is None:
        raise ValueError(f"{where} is not a git repo")
    if not workspace.git(where, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        raise ValueError(f"{where} has no commit yet")
    got = conn.execute("SELECT id FROM projects WHERE path=?", (str(where),)).fetchone()
    if got:
        return got["id"]
    branch = branch or workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    if not branch:
        raise ValueError(f"{where} is on no branch: say which with --branch")
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, check_cmd, created_at) VALUES (?,?,?,?,?,?)",
                     (pid, name or where.name, str(where), branch, check, db.now()))
    return pid


def get(conn: sqlite3.Connection, pid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def all(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()


def is_self(path: str | Path) -> bool:
    return Path(path).expanduser().resolve() == builds.source().resolve()


def repo_for(conn: sqlite3.Connection, goal: sqlite3.Row) -> Path:
    """Where a goal's branches and worktrees live."""
    pid = goal["project"] if "project" in goal.keys() else None
    if pid is None:
        return integration.repo()
    project = get(conn, pid)
    if project is None:
        raise KeyError(f"no project {pid}")
    return Path(project["path"])


def base(project: sqlite3.Row) -> str:
    """The sha at the tip of the project's branch — read, never fetched or checked out."""
    return workspace.git(project["path"], "rev-parse", "--verify", f"refs/heads/{project['branch']}^{{commit}}")


def check_argv(project: sqlite3.Row) -> Optional[str]:
    """Gate 1 as a command run's JSON argv, or None when the project has no check."""
    cmd = project["check_cmd"]
    if not cmd:
        check = Path(project["path"]) / "bin" / "check"
        if not (check.is_file() and os.access(check, os.X_OK)):
            return None
        cmd = "bin/check"
    return json.dumps(["/bin/sh", "-c", cmd, "eki-judge"])


def link_deps(project: sqlite3.Row, worktree: str | Path) -> List[str]:
    """Link the project's untracked .venv / node_modules into `worktree`, kept out of git."""
    src, dest = Path(project["path"]), Path(worktree)
    linked = []
    for name in DEPS:
        if not (src / name).exists() or workspace.git(src, "ls-files", "--", name):
            continue
        if not (dest / name).exists() and not (dest / name).is_symlink():
            os.symlink(src / name, dest / name)
        exclude = Path(workspace.git(dest, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = dest / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        have = exclude.read_text().splitlines() if exclude.exists() else []
        if name not in have:
            with open(exclude, "a") as f:
                f.write(f"\n{name}\n")
        linked.append(name)
    return linked
