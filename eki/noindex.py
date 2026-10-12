"""Spotlight out of eki's churn: the worktrees move from EKI_HOME/work to work.noindex.

macOS never indexes a folder whose name ends in .noindex, and mds_stores had
grown to 4.4 GB indexing the worktrees, gate trees and builds eki makes and
deletes all day. `paths.work()` is now `work.noindex`; builds and scratch hold
a `.metadata_never_index` marker (`paths.never_index`). `migrate` moves what
an older eki left in `work`, a step of housekeeping every pass:

(a) each child of a real `work` folder (dot-folders too) is renamed into
    work.noindex, and a symlink is left at its old name, so an absolute path a
    running worker holds keeps working; a name already taken there stays put
    and is said;
(b) the stored paths under the old root (items.worktree, threads.cwd, by the
    root as written and as resolved) are rewritten, each only when the new
    path exists and isn't one a staying child shadows;
(c) `git worktree repair` in each moved worktree, so its repo finds it;
(d) once only those symlinks are left, `work` itself becomes one symlink to
    work.noindex.

A kill anywhere is finished by the next call; with `work` a symlink or gone it
does nothing more. `home()/self` gets the marker too. Nothing under the
person's home, eki's GitHub clones, a project's folder or an assets pool is
touched.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Set

from . import paths

log = logging.getLogger("eki.noindex")

MARKER = ".metadata_never_index"
REPAIR_TIMEOUT = 20
COLUMNS = (("items", "worktree"), ("threads", "cwd"))


def migrate(conn: sqlite3.Connection) -> List[str]:
    """One line per thing done; [] when there was nothing to do."""
    said = _mark_self()
    old = paths.home() / "work"
    if old.is_symlink() or not old.is_dir():
        return said
    new = paths.work()
    olds = list(dict.fromkeys([str(old), os.path.realpath(old)]))
    staying: Set[str] = set()
    moved: List[Path] = []
    for child in sorted(old.iterdir()):
        if _ours(child, new):
            continue                                    # moved by an earlier pass
        dest = new / child.name
        if dest.exists() or dest.is_symlink():
            staying.add(child.name)
            said.append(f"spotlight: {child} stays in work, {dest.name} is already in work.noindex")
            continue
        os.rename(child, dest)
        os.symlink(dest, child)                         # an old absolute path still works meanwhile
        moved.append(dest)
        said.append(f"spotlight: moved {child.name} to work.noindex")
    said += _rewrite(conn, olds, new, staying)
    said += [line for d in moved for line in _repair(d)]
    if not staying and all(_ours(c, new) for c in old.iterdir()):
        for c in old.iterdir():
            c.unlink()
        try:
            old.rmdir()
            os.symlink(new, old)
            said.append(f"spotlight: {old} is now a symlink to work.noindex")
        except OSError as e:                            # something new landed in it: the next pass
            said.append(f"spotlight: {old} not replaced yet ({e})")
    return said


def _ours(child: Path, new: Path) -> bool:
    """A symlink this migration left: it points at work.noindex/<its name>."""
    return child.is_symlink() and os.readlink(child) == str(new / child.name)


def _mark_self() -> List[str]:
    folder = paths.home() / "self"
    if not folder.is_dir() or (folder / MARKER).exists():
        return []
    paths.never_index(folder)
    return [f"spotlight: {folder} marked never to index"]


def _rewrite(conn: sqlite3.Connection, olds: List[str], new: Path, staying: Set[str]) -> List[str]:
    said = []
    for table, col in COLUMNS:
        for root in olds:
            rows = conn.execute(f"SELECT id, {col} AS p FROM {table} WHERE {col}=? OR {col} LIKE ? ESCAPE '\\'",
                                (root, _like(root) + "/%")).fetchall()
            for row in rows:
                rest = row["p"][len(root):].lstrip("/")
                if rest.split("/", 1)[0] in staying:
                    continue
                to = str(new / rest) if rest else str(new)
                if not os.path.exists(to):
                    continue
                conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (to, row["id"]))
                said.append(f"spotlight: {table} {row['id']}: {col} now {to}")
    return said


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _repair(d: Path) -> List[str]:
    if not (d / ".git").is_file():
        return []                                       # not a git worktree
    try:
        r = subprocess.run(["git", "-C", str(d), "worktree", "repair"], capture_output=True, text=True,
                           timeout=REPAIR_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"spotlight: git worktree repair in {d.name} failed ({e})"]
    if r.returncode != 0:
        return [f"spotlight: git worktree repair in {d.name} failed ({(r.stderr or r.stdout).strip()[:200]})"]
    return []
