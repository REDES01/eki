"""Git worktrees for runs that must not collide.

A piece of work on a repo gets a checkout of its own under `EKI_HOME/work/
<key>/`: a `git worktree` on a branch of its own, from a chosen base. Two
agents in two worktrees of one repo never overwrite each other; what each
did is a branch, brought together by the queue (docs/self-build.md), not by
luck. Ignored folders a checkout needs to run (`.venv`, `node_modules`) are
linked from the source repo rather than rebuilt.

Nothing here is specific to eki's own source: any repo, any base, any key.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from . import paths

#: ignored folders a checkout needs to run, linked from the source repo
LINKED = (".venv", "venv", "node_modules")
QUIET = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=eki", "-c", "user.email=eki@localhost"]
#: a worktree untouched this long is removed by `sweep`
IDLE_DAYS = 7


class WorkspaceError(RuntimeError):
    pass


def git(where: str | Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *QUIET, "-C", str(where), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args[:2])}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def repo_of(folder: str | Path) -> Optional[str]:
    """The top level of the repo `folder` is in, or None."""
    try:
        return git(folder, "rev-parse", "--show-toplevel")
    except WorkspaceError:
        return None


def path_for(key: str) -> Path:
    return paths.work() / key


def _mark(key: str) -> Path:
    """When the worktree was last used — kept beside it, so the checkout stays clean."""
    marks = paths.work() / ".marks"
    marks.mkdir(parents=True, exist_ok=True)
    return marks / key


def add(repo: str | Path, key: str, *, base: str = "HEAD", branch: Optional[str] = None) -> Path:
    """A worktree of `repo` at `base`, on `branch` (default `eki/<key>`), under EKI_HOME/work.
    Already there: returned as it is."""
    dest = path_for(key)
    if (dest / ".git").exists():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    branch = branch or f"eki/{key}"
    if git(repo, "branch", "--list", branch):
        git(repo, "worktree", "add", "--force", str(dest), branch)
        git(dest, "reset", "--hard", sha)
    else:
        git(repo, "worktree", "add", "-b", branch, str(dest), sha)
    for name in LINKED:
        src = Path(repo) / name
        if src.is_dir() and not (dest / name).exists():
            os.symlink(src, dest / name)
    _mark(key).write_text(f"{time.time()}\n")
    return dest


def remove(repo: str | Path, key: str, *, delete_branch: bool = False) -> bool:
    dest = path_for(key)
    if not dest.exists():
        return False
    try:
        git(repo, "worktree", "remove", "--force", str(dest))
    except WorkspaceError:
        shutil.rmtree(dest, ignore_errors=True)
        git(repo, "worktree", "prune", check=False)
    if delete_branch:
        git(repo, "branch", "-D", f"eki/{key}", check=False)
    _mark(key).unlink(missing_ok=True)
    return True


def head(where: str | Path) -> str:
    return git(where, "rev-parse", "HEAD")


#: the linked folders, as pathspecs git leaves alone (a `.venv/` ignore rule
#: doesn't cover a symlink named .venv)
NOT_LINKED = [f":!{name}" for name in LINKED]


def changed(where: str | Path, since: str) -> List[str]:
    """Files different from `since` — committed or not."""
    files = set(git(where, "diff", "--name-only", since, "--", ".", *NOT_LINKED).splitlines())
    files |= set(git(where, "ls-files", "--others", "--exclude-standard", "--", ".", *NOT_LINKED).splitlines())
    return sorted(f for f in files if f)


def stage_all(where: str | Path) -> List[str]:
    """Stage everything but the linked folders; the staged paths. No pathspec on
    the add: one that matches an ignored path (a linked .venv, ignored or
    dangling) makes git refuse the whole add."""
    git(where, "add", "-A")
    for name in LINKED:                    # a link (or one an agent added): never part of a change
        if git(where, "ls-files", "--", name):
            git(where, "rm", "-q", "--cached", "--", name)
    return [f for f in git(where, "diff", "--cached", "--name-only").splitlines() if f]


def commit_all(where: str | Path, message: str) -> Optional[str]:
    """Commit everything in the worktree but the linked folders; None when nothing changed."""
    staged = stage_all(where)
    if not staged:
        return None
    git(where, "commit", "-q", "-m", message)
    return head(where)


def sweep(repo: str | Path, days: float = IDLE_DAYS) -> List[str]:
    """Remove worktrees of `repo` under EKI_HOME/work untouched for `days`."""
    gone = []
    for d in sorted(paths.work().iterdir()) if paths.work().exists() else []:
        mark = _mark(d.name)
        if d.name.startswith(".") or not mark.exists():
            continue
        if time.time() - mark.stat().st_mtime > days * 86400 and belongs(d, repo):
            remove(repo, d.name)
            gone.append(d.name)
    return gone


def belongs(worktree: str | Path, repo: str | Path) -> bool:
    try:
        common = git(worktree, "rev-parse", "--git-common-dir")
    except WorkspaceError:
        return False
    return Path(worktree, common).resolve().parent == Path(repo).resolve()


def touch(key: str) -> None:
    if _mark(key).exists():
        os.utime(_mark(key))
