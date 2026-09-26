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
import subprocess
from pathlib import Path
from typing import Dict, List

from . import builds, db, observe, paths
from .workspace import NOT_LINKED, QUIET, WorkspaceError, git

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


def carries(sha: str, ref: str = "main") -> bool:
    """`ref` has `sha`'s changes: it contains it, or every commit of its history
    that `ref` lacks has a patch-equivalent in `ref` (a rebase rewrote them).
    False for anything unknown."""
    if contains(sha, ref):
        return True
    if not sha or not _sha(sha) or not _sha(ref):
        return False
    try:
        out = git(repo(), "cherry", ref, sha)
    except WorkspaceError:
        return False
    return not any(line.startswith("+") for line in out.splitlines())


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
    (origin first). When main and origin/main have both moved, main is rebased onto
    origin/main (a conflict leaves main as it was and is written to the journal);
    the source only ever fast-forwards. Returns main's sha."""
    _fetch("origin")
    _fetch("source")
    for ref in ("origin/main", "source/main"):
        other = _sha(ref)
        if other and other != main() and contains(main(), other):
            try:
                fast_forward(other)
            except IntegrationError as e:
                log.warning("integration: fast-forward to %s failed: %s", ref, e)
        elif ref == "origin/main" and other and not contains(other, "main"):
            _rebase_onto(ref)
    return main()


def _rebase_onto(upstream: str) -> bool:
    """Rebase main onto `upstream` in a worktree of its own, so the repo's checkout
    is never mid-rebase; main moves only when it went through. True when it did."""
    r, old = repo(), main()
    wt = paths.home() / "self" / "sync-rebase"
    _drop_worktree(wt)                                # a kill mid-rebase left one: gone
    try:
        git(r, "worktree", "add", "-q", "--detach", str(wt), old)
    except WorkspaceError as e:
        log.warning("integration: no worktree to rebase in: %s", e)
        return False
    try:
        out = subprocess.run(["git", *QUIET, "-C", str(wt), "-c", "rerere.enabled=false", "rebase", "-q", upstream],
                             capture_output=True, text=True)
        if out.returncode != 0:
            git(wt, "rebase", "--abort", check=False)
            said = (out.stdout + out.stderr).strip()
            log.warning("integration: main doesn't rebase onto %s: %s", upstream, said)
            observe.record(None, "fault", data={
                "where": "integration.sync", "traceback": said[-observe.TB_MAX:],
                "frame": None, "exc": "RebaseConflict", "ours": False})
            return False
        new = git(wt, "rev-parse", "HEAD")
    finally:
        _drop_worktree(wt)
    if main() != old:                                 # main moved meanwhile: next sync
        return False
    try:
        git(r, "reset", "-q", "--keep", new)
    except WorkspaceError as e:
        log.warning("integration: main not moved to the rebased %s: %s", new[:12], e)
        return False
    log.info("integration: main %s rebased onto %s as %s", old[:12], upstream, new[:12])
    _follow_rewrite(old, upstream)
    return True


def _drop_worktree(wt: Path) -> None:
    if wt.exists():
        git(repo(), "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)
    git(repo(), "worktree", "prune", check=False)


def _patch_ids(*log_args: str) -> Dict[str, str]:
    """{patch-id: commit} for the commits `git log -p <log_args>` shows."""
    shown = subprocess.run(["git", *QUIET, "-C", str(repo()), "log", "-p", "--no-merges", *log_args],
                           capture_output=True, text=True)
    ids = subprocess.run(["git", *QUIET, "-C", str(repo()), "patch-id", "--stable"],
                         input=shown.stdout, capture_output=True, text=True)
    found: Dict[str, str] = {}
    for line in ids.stdout.splitlines():
        pid, _, sha = line.partition(" ")
        found.setdefault(pid, sha.strip())
    return found


def _follow_rewrite(old: str, upstream: str) -> None:
    """Landed and live items whose commit the rebase rewrote get `rebased` = the
    new commit with the same patch-id. Logged, never raised."""
    try:
        new_ids = _patch_ids(f"{upstream}..main")
        conn = db.connect()
        try:
            rows = conn.execute("SELECT id, rebased, commit_sha FROM items"
                                " WHERE state IN ('landed','live')").fetchall()
            moved: List[tuple] = []
            for it in rows:
                sha = it["rebased"] or it["commit_sha"]
                if not sha or contains(sha, "main") or not _sha(sha):
                    continue
                pid = next(iter(_patch_ids("-1", sha)), None)
                if pid and pid in new_ids:
                    moved.append((new_ids[pid], db.now(), it["id"]))
            if moved:
                with db.tx(conn):
                    conn.executemany("UPDATE items SET rebased=?, updated_at=? WHERE id=?", moved)
        finally:
            conn.close()
    except Exception:
        log.exception("integration: items not moved to the rebased commits")


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
