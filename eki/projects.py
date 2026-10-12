"""A person's own git folders eki works on (standing goals, docs/design.md).

A project is one row per path. eki adds worktrees and `eki/*` branches to it
and never resets, rewrites or force-pushes the project's branches. On the
local path, under autonomy 'apply' (the default), eki lands fit items on the
project's branch itself, fast-forward only (eki/projectqueue.py,
eki/projectland.py); under 'propose' the person merges `eki/<item>` when they
want it. On the GitHub path (eki/github.py) merges happen there, so new
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

#: apply: eki lands fit items on the branch; propose: the branch waits for the person
AUTONOMY = ("apply", "propose")

#: the message of the commit eki makes in a repo with none yet, when the person says yes
FIRST = "eki: the folder as it was"


class NeedsBranch(ValueError):
    """The folder has nowhere for work to land; `offer` is what eki can do about it:
    'branch' (a detached HEAD: make a branch 'main') or 'commit' (no commit yet: commit the folder)."""

    def __init__(self, folder: Path, offer: str) -> None:
        self.folder, self.offer = str(folder), offer
        if offer == "branch":
            text = (f"{folder} isn't on a branch (a detached HEAD), so eki has nowhere to land work. "
                    f"eki can make a branch 'main' here at this commit.")
        else:
            text = f"{folder} has no commit yet. eki can commit the folder as it is, so work has a starting point."
        super().__init__(text)


def _make_branch(where: Path) -> str:
    """A new branch 'main' (or main-<n> when main is taken) at the detached HEAD, switched to."""
    name, n = "main", 1
    while workspace.git(where, "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False):
        n += 1
        name = f"main-{n}"
    workspace.git(where, "switch", "-q", "-c", name)
    return name


def first_commit(where: Path) -> None:
    """The folder as it is, committed on the unborn branch; nothing pushed."""
    who: List[str] = []
    if not (workspace.git(where, "config", "user.name", check=False)
            and workspace.git(where, "config", "user.email", check=False)):
        who = ["-c", "user.name=eki", "-c", "user.email=eki@localhost"]
    workspace.git(where, "add", "-A")
    workspace.git(where, *who, "commit", "-q", "--allow-empty", "--no-verify", "-m", FIRST)


def add(conn: sqlite3.Connection, path: str | Path, *, check: Optional[str] = None,
        branch: Optional[str] = None, name: Optional[str] = None, fix: bool = False) -> str:
    """Record the git folder at `path`; the same path again is the same project. A repo with
    no commit or on no branch raises NeedsBranch, unless `fix` says yes to its offer."""
    where = Path(path).expanduser().resolve()
    if not where.is_dir() or workspace.repo_of(where) is None:
        raise ValueError(f"{where} is not a git repo")
    if not workspace.git(where, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        if not fix:
            raise NeedsBranch(where, "commit")
        first_commit(where)
    got = conn.execute("SELECT id, branch, repo, state FROM projects WHERE path=?", (str(where),)).fetchone()
    if got and got["state"] == "deleting":
        raise ValueError(f"{where} is being deleted from eki: add it again in a moment")
    if got and (got["branch"] or got["repo"]):
        return got["id"]
    branch = branch or workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    if not branch:
        if not fix:
            raise NeedsBranch(where, "branch")
        branch = _make_branch(where)
    if got:                                              # a chat-only project: its background work starts here
        with db.tx(conn):
            conn.execute("UPDATE projects SET branch=?, check_cmd=COALESCE(?, check_cmd) WHERE id=?",
                         (branch, check, got["id"]))
        return got["id"]
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, check_cmd, created_at) VALUES (?,?,?,?,?,?)",
                     (pid, name or where.name, str(where), branch, check, db.now()))
    return pid


def autonomy(project: sqlite3.Row) -> str:
    """'apply' or 'propose'. A GitHub project proposes: its work goes back as pull requests.
    A project's workflow card, when it has one, says (eki/workflow.py)."""
    if "repo" in project.keys() and project["repo"]:
        return "propose"
    from . import workflow
    card = workflow.of_project(None, project)
    return workflow.autonomy(card) if card is not None else legacy_autonomy(project)


def legacy_autonomy(project: sqlite3.Row) -> str:
    """The row's own autonomy setting: what a project with no workflow card runs under."""
    return ("autonomy" in project.keys() and project["autonomy"]) or "apply"


def set_autonomy(conn: sqlite3.Connection, pid: str, mode: str) -> None:
    if mode not in AUTONOMY:
        raise ValueError(f"autonomy is one of {', '.join(AUTONOMY)}, not {mode!r}")
    project = get(conn, pid)
    if project is None:
        raise KeyError(f"no project {pid}")
    if project["repo"]:
        raise ValueError(f"{project['repo']} is on GitHub: its work goes back as pull requests")
    with db.tx(conn):
        conn.execute("UPDATE projects SET autonomy=? WHERE id=?", (mode, pid))


def chat_history(project: sqlite3.Row) -> bool:
    """Whether the project's chat turns are recorded as commits (eki/chatcommit.py); on by default."""
    return "chat_history" not in project.keys() or project["chat_history"] is None or bool(project["chat_history"])


def set_chat_history(conn: sqlite3.Connection, pid: str, on: bool) -> None:
    if get(conn, pid) is None:
        raise KeyError(f"no project {pid}")
    with db.tx(conn):
        conn.execute("UPDATE projects SET chat_history=? WHERE id=?", (1 if on else 0, pid))


def clone_path(repo: str) -> Path:
    """Where eki keeps its own clone of OWNER/REPO."""
    owner, name = repo.split("/", 1)
    return paths.projects() / owner / name


def by_repo(conn: sqlite3.Connection, repo: str) -> Optional[sqlite3.Row]:
    """The project row for OWNER/REPO that isn't retired or deleted (a dropped one counts), or None."""
    return conn.execute("SELECT * FROM projects WHERE repo=? AND COALESCE(state, 'on') NOT IN "
                        "('retired','deleting','deleted') "
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
    one fetched); locally, or when origin has never been fetched, refs/heads/<branch>. A project
    that chose 'eki merges here' (projects.lands) is local whatever its origin: origin isn't fetched."""
    where, branch = project["path"], project["branch"]
    here = "lands" in project.keys() and project["lands"] == "here"
    if not here and github.path_of(where)[0] is not None:
        try:
            github.fetch(where, branch)
        except github.GhError:
            pass
        got = workspace.git(where, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}^{{commit}}",
                            check=False)
        if got:
            return got
    return workspace.git(where, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")


def check_argv(project: sqlite3.Row, touched: Optional[List[str]] = None) -> Optional[str]:
    """Gate 1 as a command run's JSON argv, or None when the project has no check.
    A project's workflow card, when it has one, holds the command (eki/workflow.py); a card
    with path rules runs the rules matching `touched`, the files the change touched."""
    from . import workflow
    card = workflow.of_project(None, project)
    cmd = workflow.check_line(card, touched or []) if card is not None else legacy_check(project)
    return json.dumps(["/bin/sh", "-c", cmd, "eki-judge"]) if cmd else None


def batch_argv(project: sqlite3.Row) -> Optional[str]:
    """Gate 2 as a command run's JSON argv: the card's batch_command (its command when that is
    text), else the row's own check; None when there is nothing to run."""
    from . import workflow
    card = workflow.of_project(None, project)
    cmd = (card.batch_command or None) if card is not None else legacy_check(project)
    return json.dumps(["/bin/sh", "-c", cmd, "eki-judge"]) if cmd else None


def legacy_check(project: sqlite3.Row) -> Optional[str]:
    """The check command the row's settings mean: its check_cmd, else an executable bin/check."""
    cmd = project["check_cmd"]
    if ("repo" in project.keys() and project["repo"]) or cmd:   # eki's clone: the guess or the person's word only
        return cmd or None
    check = Path(project["path"]) / "bin" / "check"
    return "bin/check" if check.is_file() and os.access(check, os.X_OK) else None


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
