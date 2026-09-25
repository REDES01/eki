"""Rebase mechanics: the git steps the queue and the resolve path use.

An item's branch is rebased onto its predicted head in the item's own
worktree (docs/self-build.md, "The queue"). A clean rebase leaves the branch
on top of the head; a conflict leaves the worktree mid-rebase for a resolver,
and `proceed` carries it on once the resolver is done. Gate 2 runs in a fresh
detached worktree (`gate_tree`).

Pure git, no database. Every function is safe to call again after a kill:
`onto` aborts a leftover rebase before it starts, `gate_tree` removes what
was there before it adds.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List

from . import paths, workspace
from .workspace import LINKED, NOT_LINKED, WorkspaceError, git

#: nothing waits for an editor: `rebase --continue` keeps the message it has
NO_EDITOR = {"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true"}
#: a rebase that stops more often than this in one `proceed` is not moving
MAX_STEPS = 200


def _run(where: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *workspace.QUIET, "-C", str(where), *args],
                          capture_output=True, text=True, env={**os.environ, **NO_EDITOR})


def _git_path(worktree: str | Path, name: str) -> Path:
    p = Path(git(worktree, "rev-parse", "--git-path", name))
    return p if p.is_absolute() else Path(worktree) / p


def in_progress(worktree: str | Path) -> bool:
    """A rebase is stopped in this worktree."""
    return any(_git_path(worktree, n).exists() for n in ("rebase-merge", "rebase-apply"))


def abort(worktree: str | Path) -> None:
    if in_progress(worktree):
        out = _run(worktree, "rebase", "--abort")
        if out.returncode != 0 and in_progress(worktree):
            raise WorkspaceError(f"git rebase --abort: {out.stderr.strip() or out.stdout.strip()}")


def conflicted(worktree: str | Path) -> List[str]:
    """Files with unmerged entries right now."""
    out = git(worktree, "diff", "--name-only", "--diff-filter=U")
    return sorted(set(f for f in out.splitlines() if f))


def _stopped(worktree: str | Path, out: subprocess.CompletedProcess, what: str) -> List[str]:
    """After a rebase command that failed: the conflicted files, or on with the rebase."""
    if not in_progress(worktree):
        raise WorkspaceError(f"git {what}: {out.stderr.strip() or out.stdout.strip()}")
    files = conflicted(worktree)
    return files if files else proceed(worktree)


def onto(worktree: str | Path, head: str) -> List[str]:
    """Rebase the worktree's branch onto `head`. [] when it finished cleanly;
    otherwise the conflicted files, the worktree left mid-rebase."""
    abort(worktree)
    sha = git(worktree, "rev-parse", "--verify", f"{head}^{{commit}}")
    if _run(worktree, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0:
        return []                               # already on head
    out = _run(worktree, "rebase", sha)
    if out.returncode == 0 and not in_progress(worktree):
        return []
    return _stopped(worktree, out, "rebase")


def _stage(worktree: str | Path) -> None:
    """Everything but the linked folders, like workspace.commit_all."""
    for name in LINKED:
        if git(worktree, "ls-files", "--", name):
            git(worktree, "rm", "-q", "--cached", "--", name)
    git(worktree, "add", "-A", "--", ".", *NOT_LINKED)


def proceed(worktree: str | Path) -> List[str]:
    """Stage the resolution and continue the rebase. [] when it is finished;
    the newly conflicted files when it stopped again on a later commit."""
    for _ in range(MAX_STEPS):
        if not in_progress(worktree):
            return []
        _stage(worktree)
        out = _run(worktree, "rebase", "--continue")
        if out.returncode == 0 and not in_progress(worktree):
            return []
        if not in_progress(worktree):
            raise WorkspaceError(f"git rebase --continue: {out.stderr.strip() or out.stdout.strip()}")
        files = conflicted(worktree)
        if files:
            return files
        if out.returncode != 0:                 # the step became empty: skip it
            skip = _run(worktree, "rebase", "--skip")
            if skip.returncode != 0 and in_progress(worktree) and conflicted(worktree):
                return conflicted(worktree)
    raise WorkspaceError("git rebase: not moving")


def _has_marker(path: Path) -> bool:
    try:
        text = path.read_text(errors="replace")
    except (OSError, UnicodeError):
        return False
    opened = False
    for line in text.splitlines():
        if line.startswith("<<<<<<< ") or line.startswith(">>>>>>> "):
            return True
        if line.startswith("<<<<<<<"):
            opened = True
        elif line == "=======" and opened:
            return True
    return False


def markers(worktree: str | Path, since: str) -> List[str]:
    """Files changed since `since` (and in the working tree) that still hold a conflict marker."""
    files = set(workspace.changed(worktree, since)) | set(conflicted(worktree))
    files |= set(git(worktree, "diff", "--name-only", "HEAD", "--", ".", *NOT_LINKED).splitlines())
    root = Path(worktree)
    return sorted(f for f in files
                  if f and f.split("/")[0] not in LINKED and (root / f).is_file()
                  and not (root / f).is_symlink() and _has_marker(root / f))


def gate_tree(repo: str | Path, key: str, sha: str) -> Path:
    """A fresh detached worktree of `repo` at `sha` under EKI_HOME/work/<key>,
    the linked folders symlinked from `repo`. Removal: workspace.remove(repo, key)."""
    dest = paths.work() / key
    workspace.remove(repo, key)
    git(repo, "worktree", "prune", check=False)
    commit = git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    git(repo, "worktree", "add", "--force", "--detach", str(dest), commit)
    for name in LINKED:
        src = Path(repo) / name
        if src.is_dir() and not (dest / name).exists():
            os.symlink(src, dest / name)
    return dest
