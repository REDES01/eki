"""Land work on a person's branch: fast-forward only, and the folder only when it's safe.

Pure git, no database. `land` moves refs/heads/<branch> forward to a commit
that contains its tip. When the branch is checked out in a folder, the folder
moves with it through `git merge --ff-only`, and only when no file the change
touches has local changes there; otherwise nothing moves and the why names
the files. Never a reset, a force, a checkout of another branch or a clean:
untracked and ignored files are never touched. Every call is safe to make
again after a kill: a branch that already contains the commit is done.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Set

from . import workspace


class NotFastForward(ValueError):
    """The commit doesn't contain the branch's tip: landing it would rewrite the branch."""


def checked_out(repo: str | Path, branch: str) -> Optional[Path]:
    """The worktree of `repo` (the main one included) with refs/heads/<branch> checked out, or None."""
    out = workspace.git(repo, "worktree", "list", "--porcelain")
    where: Optional[str] = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            where = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and where:
            return Path(where)
    return None


def _names(where: str | Path, *args: str) -> Set[str]:
    out = workspace.git(where, *args, "-z")
    return {n for n in out.split("\0") if n}


def blockers(folder: str | Path, old: str, new: str) -> List[str]:
    """Paths in `folder` that would stop a fast-forward from `old` to `new`: tracked files with
    staged or unstaged changes that old..new also changes, and untracked or ignored files where
    `new` adds one. Nothing else in the folder counts."""
    folder = Path(folder)
    changed = _names(folder, "diff", "--name-only", "--no-renames", old, new)
    dirty = (_names(folder, "diff", "--name-only", "--no-renames")
             | _names(folder, "diff", "--name-only", "--no-renames", "--cached"))
    found = changed & dirty
    for path in _names(folder, "diff", "--name-only", "--no-renames", "--diff-filter=A", old, new):
        if path in found:
            continue
        if os.path.lexists(folder / path):
            found.add(path)
            continue
        parts = path.split("/")
        for i in range(1, len(parts)):           # a file where the new one needs a folder
            parent = "/".join(parts[:i])
            if os.path.lexists(folder / parent) and not (folder / parent).is_dir():
                if not workspace.git(folder, "ls-files", "--", parent):
                    found.add(parent)
                break
    return sorted(found)


def _ok(where: str | Path, *args: str) -> Optional[str]:
    """None when git succeeds, else the first line it said."""
    try:
        workspace.git(where, *args)
        return None
    except workspace.WorkspaceError as e:
        said = str(e).split(": ", 1)[-1].strip()
        return (said.splitlines() or ["git refused"])[0]


def _contains(repo: str | Path, ancestor: str, tip: str) -> bool:
    return _ok(repo, "merge-base", "--is-ancestor", ancestor, tip) is None


def land(repo: str | Path, branch: str, new: str) -> Optional[str]:
    """Move refs/heads/<branch> forward to `new`. None once landed (or already there); otherwise
    why it waits, with nothing changed. Raises NotFastForward when `new` doesn't contain the tip."""
    new = workspace.git(repo, "rev-parse", "--verify", f"{new}^{{commit}}")
    tip = workspace.git(repo, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    if _contains(repo, new, tip):
        return None
    if not _contains(repo, tip, new):
        raise NotFastForward(f"{new[:12]} doesn't contain the tip of {branch} ({tip[:12]})")
    folder = checked_out(repo, branch)
    if folder is not None:
        files = blockers(folder, tip, new)
        if files:
            return "has local changes to " + ", ".join(files)
        said = _ok(folder, "merge", "--ff-only", "-q", new)
        return None if said is None else "has local changes: " + said
    if _ok(repo, "update-ref", "-m", f"eki: land {new[:12]}", f"refs/heads/{branch}", new, tip):
        now = workspace.git(repo, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
        if _contains(repo, new, now):
            return None
        return f"{branch} moved while landing"
    return None
