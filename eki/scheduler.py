"""Starting items: which waiting items may build now (docs/self-build.md, "Items").

An item starts once its deps are fit and its write-set doesn't overlap what
is already building, judging or being reviewed, up to `self.parallel` at once. Nothing is
kept between ticks but a cache of the tracked files, cleared at every pass.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Set

from . import db, workspace


def start_ready(conn: sqlite3.Connection) -> List[str]:
    from . import selfwork              # selfwork calls this from its tick: import it late
    _tracked.clear()
    settings = selfwork.settings()
    parallel = int(settings["parallel"])
    live = selfwork.items_in(conn, selfwork.LIVE)
    if len(live) >= parallel:
        return []
    done_ids = {r["id"] for r in selfwork.items_in(conn, selfwork.FIT.get(settings["autonomy"],
                                                                          selfwork.FIT["propose"]))}
    taken: List[Set[str]] = [claims(x) for x in live]
    names = None
    said = []
    for it in selfwork.items_in(conn, ("waiting",)):
        if len(live) >= parallel:
            break
        deps = json.loads(it["deps"] or "[]")
        if any(d not in done_ids for d in deps):
            continue
        if names is None:
            names = tracked(selfwork.repo())
        mine = expand(json.loads(it["files"] or "[]"), names)
        if not it["independent"] and any(mine & t for t in taken):
            continue
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
        others = [(x["title"], json.loads(x["files"] or "[]")) for x in live]
        with db.tx(conn):
            selfwork.start_build(conn, it, goal, others)
        live.append(selfwork.store_item(conn, it["id"]))
        taken.append(mine)
        said.append(f"item {it['id']}: building ({it['title'][:50]})")
    return said


def claims(it: sqlite3.Row) -> Set[str]:
    from . import selfwork
    files = set(json.loads(it["files"] or "[]")) | set(json.loads(it["touched"] or "[]"))
    return expand(sorted(files), tracked(selfwork.repo()))


# ---- write-sets ------------------------------------------------------------------------------

_tracked: Dict[str, List[str]] = {}


def tracked(repo: Path) -> List[str]:
    key = str(repo)
    if key not in _tracked:
        _tracked[key] = workspace.git(repo, "ls-files").splitlines()
    return _tracked[key]


def expand(patterns: List[str], names: List[str]) -> Set[str]:
    """The files a write-set means. No write-set means everything."""
    if not patterns:
        return set(names) | {"*"}
    out: Set[str] = set()
    for p in patterns:
        p = p.strip().lstrip("./")
        if p.endswith("/"):
            p += "*"
        hits = fnmatch.filter(names, p)
        out |= set(hits) if hits else {p}          # a file that doesn't exist yet: by name
    return out
