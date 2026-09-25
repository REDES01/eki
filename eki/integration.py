"""The repo eki lands into (docs/self-build.md, "The integration repo").

    EKI_HOME/self/repo      a clone eki owns, `main` checked out
        origin  -> where the source checkout pushes (GitHub)
        source  -> the source checkout itself (~/eki), one more git peer

Changes land here by fast-forwarding `main`; `main` is pushed to origin,
and the source checkout is fast-forwarded only when it is clean and on
`main` — otherwise the person pulls. Nothing here touches the source
checkout except `update_source`. Every step is an idempotent git operation:
a kill between any two leaves something the next call picks up.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from . import builds, paths
from .workspace import NOT_LINKED, WorkspaceError, git

log = logging.getLogger("eki.integration")


class IntegrationError(RuntimeError):
    pass


def repo() -> Path:
    """EKI_HOME/self/repo, cloned from the source checkout on first use."""
    dest = paths.home() / "self" / "repo"
    if (dest / ".git").exists():
        return dest
    if dest.exists():                                 # half-made: made again
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in dest.parent.glob("repo.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    src = builds.source()
    tmp = dest.parent / f"repo.tmp-{os.getpid()}"
    try:
        git(dest.parent, "clone", "-q", "--origin", "source", "--branch", "main", str(src), str(tmp))
        url = git(src, "remote", "get-url", "origin", check=False) or str(src)
        git(tmp, "remote", "add", "origin", url)
        git(tmp, "config", "rerere.enabled", "true")
        git(tmp, "config", "rerere.autoupdate", "true")
        if (src / ".venv").is_dir():
            os.symlink(src / ".venv", tmp / ".venv")
            with open(tmp / ".git" / "info" / "exclude", "a") as f:
                f.write("\n.venv\n")
        os.rename(tmp, dest)
    except (WorkspaceError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise IntegrationError(f"cannot make the integration repo from {src}: {e}") from e
    _fetch("origin")
    return dest


def _fetch(remote: str) -> bool:
    try:
        git(repo(), "fetch", "-q", "--prune", remote)
        return True
    except WorkspaceError as e:
        log.warning("integration: fetch %s failed: %s", remote, e)
        return False


def _sha(ref: str) -> str:
    return git(repo(), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)


def main() -> str:
    """The sha of main in the integration repo."""
    return git(repo(), "rev-parse", "main")


def contains(sha: str, ref: str = "main") -> bool:
    """Is `sha` `ref` or an ancestor of it; False for anything unknown."""
    if not sha or not _sha(sha) or not _sha(ref):
        return False
    try:
        git(repo(), "merge-base", "--is-ancestor", sha, ref)
        return True
    except WorkspaceError:
        return False


def fast_forward(sha: str) -> None:
    """Move main to `sha`; IntegrationError when that isn't a fast-forward."""
    target = _sha(sha)
    if not target:
        raise IntegrationError(f"unknown commit {sha}")
    if target == main():
        return
    if not contains(main(), target):
        raise IntegrationError(f"{sha[:12]} is not a fast-forward of main {main()[:12]}")
    try:
        git(repo(), "merge", "-q", "--ff-only", target)
    except WorkspaceError as e:
        raise IntegrationError(str(e)) from e


def sync() -> str:
    """Fetch origin and source; fast-forward main to whichever strictly contains it
    (origin first). Diverged: main stays as it is. Returns main's sha."""
    _fetch("origin")
    _fetch("source")
    for ref in ("origin/main", "source/main"):
        other = _sha(ref)
        if other and other != main() and contains(main(), other):
            try:
                fast_forward(other)
            except IntegrationError as e:
                log.warning("integration: fast-forward to %s failed: %s", ref, e)
    return main()


def push() -> bool:
    """Push main to origin when it's ahead; True when origin has main afterwards.
    A failure is logged and False — it's retried by being called again."""
    try:
        if _sha("origin/main") == main():
            return True
        git(repo(), "push", "-q", "origin", "main:main")
        return True
    except (WorkspaceError, IntegrationError) as e:
        log.warning("integration: push failed: %s", e)
        return False


def update_source() -> bool:
    """Fast-forward the source checkout to integration main, only when it is on
    main, clean, and behind. True when it moved; never raises."""
    src = builds.source()
    try:
        target = main()
        if git(src, "symbolic-ref", "-q", "--short", "HEAD", check=False) != "main":
            return False
        if git(src, "status", "--porcelain", "--", ".", *NOT_LINKED):
            return False
        here = git(src, "rev-parse", "HEAD")
        if here == target or not contains(here, "main"):
            return False
        git(src, "fetch", "-q", str(repo()), "main")
        git(src, "merge", "-q", "--ff-only", target)
        return True
    except (WorkspaceError, IntegrationError, OSError) as e:
        log.warning("integration: source not updated: %s", e)
        return False
