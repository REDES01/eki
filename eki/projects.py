"""A person's own git folders eki works on (standing goals, docs/design.md).

A project is one row per path. eki only ever adds worktrees and `eki/*`
branches to it: it never checks out, commits to, resets or pushes the
project's branches. On the local path the person merges `eki/<item>` when
they want it; on the GitHub path (eki/github.py) merges happen there, so new
work is based on origin's branch, fetched into refs/remotes/origin only. A goal with `project` NULL is eki itself (the integration repo).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from . import builds, db, github, integration, paths, store, workspace

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


def watch(conn: sqlite3.Connection, folder: str | Path, *, check: Optional[str] = None,
          branch: Optional[str] = None) -> str:
    """Record the project and take work from its issues labelled eki (eki/issues.py)."""
    pid = add(conn, folder, check=check, branch=branch)
    with db.tx(conn):
        conn.execute("UPDATE projects SET issues=1 WHERE id=?", (pid,))
    return pid


def unwatch(conn: sqlite3.Connection, folder: str | Path) -> str:
    """Stop taking work from its issues; KeyError when the folder isn't a project."""
    where = Path(folder).expanduser().resolve()
    got = conn.execute("SELECT id FROM projects WHERE path=?", (str(where),)).fetchone()
    if got is None:
        raise KeyError(f"{where} is not a project")
    with db.tx(conn):
        conn.execute("UPDATE projects SET issues=0 WHERE id=?", (got["id"],))
    return got["id"]


def clone_path(repo: str) -> Path:
    """Where eki keeps its own clone of OWNER/REPO."""
    owner, name = repo.split("/", 1)
    return paths.projects() / owner / name


def by_repo(conn: sqlite3.Connection, repo: str) -> Optional[sqlite3.Row]:
    """The project row for OWNER/REPO that isn't retired (a dropped one counts), or None."""
    return conn.execute("SELECT * FROM projects WHERE repo=? AND COALESCE(state, 'on')!='retired' "
                        "ORDER BY created_at DESC LIMIT 1", (repo,)).fetchone()


def open_prs(conn: sqlite3.Connection) -> Dict[str, int]:
    """Project name -> its proposed items with a pull request still open."""
    rows = conn.execute(
        "SELECT p.name, COUNT(*) AS n FROM items i JOIN goals g ON g.id=i.goal_id "
        "JOIN projects p ON p.id=g.project WHERE i.state='proposed' AND i.pr_state='open' "
        "GROUP BY p.name ORDER BY p.name").fetchall()
    return {r["name"]: r["n"] for r in rows}


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
    """The sha at the tip of the project's branch, never checked out. On the GitHub path that is
    origin's branch, fetched first into refs/remotes/origin/<branch> (a failed fetch keeps the last
    one fetched); locally, or when origin has never been fetched, refs/heads/<branch>."""
    where, branch = project["path"], project["branch"]
    if github.path_of(where)[0] is not None:
        try:
            github.fetch(where, branch)
        except github.GhError:
            pass
        got = workspace.git(where, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}^{{commit}}",
                            check=False)
        if got:
            return got
    return workspace.git(where, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")


def check_argv(project: sqlite3.Row) -> Optional[str]:
    """Gate 1 as a command run's JSON argv, or None when the project has no check."""
    cmd = project["check_cmd"]
    if "repo" in project.keys() and project["repo"]:     # eki's clone: the guess or the person's word only
        return json.dumps(["/bin/sh", "-c", cmd, "eki-judge"]) if cmd else None
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
