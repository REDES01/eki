"""The hard lock list: files eki may never change on its own say.

docs/self-build.md, 'What eki may not change alone': the launcher, the
drill, the check and the few modules the rest trusts to stay honest. An
item that touches one of them waits for a person's yes.
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import List

HARD_LOCKED = (
    "bin/eki-launcher",
    "eki/launchd.py",
    "bin/check",
    "eki/drill.py",
    "eki/providers/base.py",
    "eki/quota.py",
    "LICENSE",
)


def _norm(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def locked_in(files: List[str]) -> List[str]:
    """The hard-locked paths among files (globs count when they match one), sorted."""
    pats = [_norm(f) for f in files]
    return sorted(p for p in HARD_LOCKED if any(p == f or fnmatch(p, f) for f in pats))
