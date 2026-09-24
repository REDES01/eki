# SPDX-License-Identifier: Apache-2.0
"""`@file` completion for the composer: paths in the thread's folder that
match what was typed, the way the terminals offer them.

The program's own `file_suggestions` answers only an empty query on the
current build, and Codex has a different call, so eki matches itself: the
files git tracks (plus untracked, minus ignored) when the folder is a
repo, a bounded walk otherwise. Cached briefly per folder."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, List, Tuple

_cache: Dict[str, Tuple[float, List[str]]] = {}
TTL = 20.0
LIMIT = 20_000
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist",
        ".DS_Store", "Eki.app", ".mypy_cache", ".cache"}


def listing(cwd: str) -> List[str]:
    now = time.time()
    hit = _cache.get(cwd)
    if hit and now - hit[0] < TTL:
        return hit[1]
    paths: List[str] = []
    try:
        out = subprocess.run(["git", "-C", cwd, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             capture_output=True, timeout=10)
        if out.returncode == 0:
            paths = [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = []
    if not paths:
        for root, dirs, files in os.walk(cwd):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            rel = os.path.relpath(root, cwd)
            for f in files:
                if f in SKIP:
                    continue
                paths.append(f if rel == "." else os.path.join(rel, f))
                if len(paths) >= LIMIT:
                    break
            if len(paths) >= LIMIT:
                break
    # folders too, so "@src/" completes
    dirs = sorted({os.path.dirname(p) + "/" for p in paths if os.path.dirname(p)})
    paths = dirs + paths
    _cache[cwd] = (now, paths)
    return paths


def suggest(cwd: str, query: str, limit: int = 12) -> List[Dict[str, str]]:
    """Matches for what's typed after "@": prefix of the name first, then
    prefix of the path, then anywhere in the path; case-insensitive."""
    if not cwd or not os.path.isdir(cwd):
        return []
    q = (query or "").lower()
    paths = listing(cwd)
    if not q:
        return [{"path": p} for p in paths[:limit]]
    name_pre, path_pre, within = [], [], []
    for p in paths:
        lp = p.lower()
        base = os.path.basename(lp.rstrip("/"))
        if base.startswith(q):
            name_pre.append(p)
        elif lp.startswith(q):
            path_pre.append(p)
        elif q in lp:
            within.append(p)
        if len(name_pre) >= limit:
            break
    out = (name_pre + path_pre + within)[:limit]
    return [{"path": p} for p in out]


# ---- files a run made --------------------------------------------------------------

# what the gallery can show: pictures, pages, drawings, diagrams — the same
# kinds it finds written out in an answer
MADE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".html", ".htm", ".svg", ".mmd", ".mermaid"}
MADE_LIMIT = 60


def made(paths: List[str]) -> List[str]:
    """The files among these the gallery can show and that are still there.

    Claude Code and Codex write their work into the folder rather than into
    the answer, so without this a page an agent built never reaches the
    gallery."""
    out: List[str] = []
    for p in paths:
        if os.path.splitext(p)[1].lower() in MADE and os.path.isfile(p) and p not in out:
            out.append(p)
            if len(out) >= MADE_LIMIT:
                break
    return out


def made_since(folder: str, since: float) -> List[str]:
    """What a run worked in place changed in the folder, by the clock: a
    folder that isn't a git repo has no diff to read, and runs there take
    turns, so what changed while it ran is what it wrote."""
    if not folder or not os.path.isdir(folder):
        return []
    found: List[str] = []
    seen = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for f in files:
            seen += 1
            if seen > LIMIT:
                return made(found)
            if os.path.splitext(f)[1].lower() not in MADE:
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getmtime(p) >= since:
                    found.append(p)
            except OSError:
                continue
    return made(found)
