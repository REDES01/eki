"""Dependencies stay links: put back a node_modules an agent installed in a worktree.

A worktree sees the project folder's `.venv`, `venv` and `node_modules` by
symlink (eki/workspace.py LINKED). An agent that runs `npm install` inside
the worktree replaces the link with a real folder — a hundred megabytes per
worktree, and a gate that runs on dependencies the person never installed.
At judge time `restore` removes that folder and links the source's again.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List

from .workspace import LINKED

log = logging.getLogger("eki.deplinks")


def _tracked(source: Path, name: str) -> bool:
    """Does git track anything under `name` in the source checkout? False when it can't tell."""
    try:
        out = subprocess.run(["git", "-C", str(source), "ls-files", "--", name],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def restore(worktree: str | Path, source: str | Path) -> List[str]:
    """Relink each dependency folder the worktree replaced with a real one; the names restored."""
    worktree, source = Path(worktree), Path(source)
    done = []
    for name in LINKED:
        src, here = source / name, worktree / name
        if src.is_symlink() or not src.is_dir() or here.is_symlink() or not here.is_dir():
            continue
        if _tracked(source, name):
            continue
        try:
            shutil.rmtree(here)
            os.symlink(src, here)
        except OSError as e:               # never breaks judging: the folder stays, said in the log
            log.warning("deplinks: couldn't relink %s in %s: %s", name, worktree, e)
            continue
        done.append(name)
    return done
