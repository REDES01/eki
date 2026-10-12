"""Keeping a project's media pool out of git: what adding an assets card does to the repo.

`settle(root, rel)` git-ignores the pool (`/<rel>/` in root/.gitignore) and takes
whatever git tracks inside it out of the index, kept on disk, then commits just
that — built on a scratch index from HEAD, so the person's other staged or
unstaged changes stay as they were. It says what it did in plain words; with
no repo, or nothing to do, it says nothing. A git failure never raises: it
comes back as a line saying what didn't happen and why.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

ME = ["-c", "user.name=eki", "-c", "user.email=eki@localhost"]


class _Failed(Exception):
    pass


def _git(root: Path, *args: str, env: Optional[Dict[str, str]] = None,
         stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=30, input=stdin, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def _must(root: Path, *args: str, **kw) -> str:
    got = _git(root, *args, **kw)
    if got.returncode != 0:
        lines = (got.stderr or got.stdout).strip().splitlines()
        raise _Failed(lines[-1] if lines else f"git {args[0]} failed")
    return got.stdout


def _blob(root: Path, spec: str) -> Optional[str]:
    """The object id at `spec` (HEAD:.gitignore, :.gitignore), None when there is none."""
    got = _git(root, "rev-parse", "-q", "--verify", spec)
    return got.stdout.strip() if got.returncode == 0 else None


def _with_line(text: str, line: str) -> str:
    return text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"


def settle(root: Path, rel: str) -> List[str]:
    """Git-ignore the pool at root/rel and untrack what is tracked in it; what was done, in words."""
    root = Path(root)
    rel = rel.strip("/")
    if _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return []
    ign = _git(root, "check-ignore", "-q", "--no-index", rel + "/")
    add_line = ign.returncode == 1
    if ign.returncode not in (0, 1):
        return [f"didn't git-ignore {rel}/: {ign.stderr.strip() or 'git check-ignore failed'}"]
    ls = _git(root, "ls-files", "-z", "--", rel)
    if ls.returncode != 0:
        return [f"didn't untrack {rel}/: {ls.stderr.strip() or 'git ls-files failed'}"]
    tracked = [p for p in ls.stdout.split("\0") if p]
    if not add_line and not tracked:
        return []
    try:
        return _apply(root, rel, add_line, tracked)
    except _Failed as e:
        return [f"didn't keep {rel}/ out of git: {e}"]
    except OSError as e:
        return [f"didn't keep {rel}/ out of git: {e}"]


def _apply(root: Path, rel: str, add_line: bool, tracked: List[str]) -> List[str]:
    line = f"/{rel}/"
    head = _blob(root, "HEAD")
    gi = root / ".gitignore"
    new_blob, old = None, _blob(root, "HEAD:.gitignore")
    if add_line:
        base = _must(root, "cat-file", "blob", old) if old else ""
        new_blob = _must(root, "hash-object", "-w", "--stdin", stdin=_with_line(base, line)).strip()
    # the commit: HEAD with just this change, on a scratch index
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if head:
            _must(root, "read-tree", "HEAD", env=env)
        if new_blob:
            _must(root, "update-index", "--add", "--cacheinfo", f"100644,{new_blob},.gitignore", env=env)
        _must(root, "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", rel, env=env)
        tree = _must(root, "write-tree", env=env).strip()
    who = [] if (_git(root, "config", "user.name").stdout.strip()
                 and _git(root, "config", "user.email").stdout.strip()) else ME
    msg = f"eki: keep {rel}/ out of git (media pool)"
    commit = _must(root, *who, "commit-tree", tree, *(["-p", head] if head else []), "-m", msg).strip()
    _must(root, "update-ref", "-m", msg, "HEAD", commit, *([head] if head else []))
    # the person's index and folder follow, leaving their own changes as they were
    said = []
    if add_line:
        if _blob(root, ":.gitignore") == old:   # nothing of theirs staged there
            _must(root, "update-index", "--add", "--cacheinfo", f"100644,{new_blob},.gitignore")
        text = gi.read_text() if gi.exists() else ""
        gi.write_text(_with_line(text, line))
        said.append(f"wrote {line} to .gitignore")
    if tracked:
        _must(root, "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", rel)
        n = len(tracked)
        said.append(f"took {n} file{'' if n == 1 else 's'} in {rel}/ out of git (kept on disk)")
    return said
