"""Finding markdown to open in the side panel ("Open file…", ⌘O): a fuzzy match on the path
under a project's folder, or under every project's folder when none is open."""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, Iterator, List, Optional, Tuple

from . import projects

EXTS = (".md", ".markdown")
SKIP = {".git", "node_modules"}
PER_ROOT = 5000
LIMIT = 50
NEWEST = 20


def roots(conn: sqlite3.Connection, project: Optional[str]) -> List[str]:
    """The project's folder, or every project's that is set and exists; KeyError for an unknown one."""
    if project:
        row = projects.get(conn, project)
        if row is None:
            raise KeyError(f"no project {project}")
        rows = [row]
    else:
        rows = projects.all(conn)
    out: List[str] = []
    for r in rows:
        folder = r["folder"]
        if folder and os.path.isdir(folder) and os.path.realpath(folder) not in out:
            out.append(os.path.realpath(folder))
    return out


def walk(root: str) -> Iterator[Tuple[str, str, float]]:
    """(path, rel, mtime) of each markdown file under `root`, no dot-folders or node_modules."""
    n = 0
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        for name in sorted(names):
            if not name.lower().endswith(EXTS):
                continue
            path = os.path.join(here, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            yield path, os.path.relpath(path, root), mtime
            n += 1
            if n >= PER_ROOT:
                return


def _subsequence(q: str, s: str) -> bool:
    it = iter(s)
    return all(ch in it for ch in q)


def search(conn: sqlite3.Connection, q: str, project: Optional[str]) -> List[Dict[str, object]]:
    """Up to 50 files: substring matches before subsequence ones, shorter paths, then newest.
    An empty `q` is the 20 newest."""
    q = (q or "").strip().lower()
    found = []
    for root in roots(conn, project):
        for path, rel, mtime in walk(root):
            low = rel.lower()
            rank = 0 if not q or q in low else 1 if _subsequence(q, low) else None
            if rank is None:
                continue
            found.append((rank, len(rel), -mtime, {"path": path, "rel": rel, "root": root, "mtime": mtime}))
    if not q:
        found.sort(key=lambda f: f[2])
        return [f[3] for f in found[:NEWEST]]
    found.sort(key=lambda f: f[:3])
    return [f[3] for f in found[:LIMIT]]
