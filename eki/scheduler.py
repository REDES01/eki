"""Starting items: which waiting items may build now (docs/self-build.md, "Items").

Waiting items are taken goal by goal, round-robin: the goal with the fewest
items building goes first, then the older goal, then the older item. An item
starts once its deps are fit and its write-set doesn't overlap what is
building, judging or being reviewed in the same repo (items of two projects
never block each other), up to `self.parallel` building at once — judging
and review are held elsewhere.
An item left waiting says why in `items.why`, written only when it changes.
Nothing is kept between ticks but a cache of the tracked files, cleared at
every pass.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import db, projects, workspace

NO_WRITE_SET = "no write-set: waits until its repo is quiet"


def start_ready(conn: sqlite3.Connection) -> List[str]:
    from . import selfwork              # selfwork calls this from its tick: import it late
    _tracked.clear()
    settings = selfwork.settings()
    parallel = int(settings["parallel"])
    fit = selfwork.FIT.get(settings["autonomy"], selfwork.FIT["propose"])
    done_ids = {r["id"] for r in selfwork.items_in(conn, fit)}
    goals: Dict[str, Optional[sqlite3.Row]] = {}
    repos: Dict[str, Optional[Path]] = {}

    def goal_of(gid: str) -> Optional[sqlite3.Row]:
        if gid not in goals:
            goals[gid] = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
        return goals[gid]

    def repo_of(gid: str) -> Optional[Path]:
        if gid not in repos:
            g = goal_of(gid)
            try:
                repos[gid] = projects.repo_for(conn, g) if g is not None else None
            except KeyError:
                repos[gid] = None
        return repos[gid]

    live = selfwork.items_in(conn, selfwork.LIVE)
    taken: Dict[str, List[Tuple[str, Set[str]]]] = {}     # repo -> [(item id, its files)]
    for x in live:
        r = repo_of(x["goal_id"])
        if r is not None:
            taken.setdefault(str(r), []).append((x["id"], claims(x, r)))
    building: Dict[str, int] = {}
    for x in live:
        if x["state"] == "building":
            building[x["goal_id"]] = building.get(x["goal_id"], 0) + 1

    waiting: Dict[str, List[sqlite3.Row]] = {}
    for it in selfwork.items_in(conn, ("waiting",)):
        waiting.setdefault(it["goal_id"], []).append(it)

    def blocker(it: sqlite3.Row) -> Optional[str]:
        missing = [d for d in json.loads(it["deps"] or "[]") if d not in done_ids]
        if missing:
            dep = conn.execute("SELECT title FROM items WHERE id=?", (missing[0],)).fetchone()
            return f"after {dep['title'] if dep else missing[0]}"
        r = repo_of(it["goal_id"])
        if r is None:
            return "its project is gone"
        if it["independent"]:
            return None
        files = json.loads(it["files"] or "[]")
        mine = expand(files, tracked(r))
        for oid, theirs in taken.get(str(r), []):
            both = (mine & theirs) - {"*"}
            if both or ("*" in mine and "*" in theirs):
                return NO_WRITE_SET if not files else f"shares {sorted(both)[0] if both else '*'} with {oid}"
        return None

    said = []
    started = True
    while started and sum(building.values()) < parallel:
        started = False
        order = sorted((g for g in waiting if waiting[g] and goal_of(g) is not None),
                       key=lambda g: (building.get(g, 0), goal_of(g)["created_at"], g))
        for gid in order:
            if sum(building.values()) >= parallel:
                break
            it = next((x for x in waiting[gid] if blocker(x) is None), None)
            if it is None:
                continue
            r = repo_of(gid)
            others = [(x["title"], json.loads(x["files"] or "[]")) for x in live
                      if repo_of(x["goal_id"]) == r]
            with db.tx(conn):
                selfwork.start_build(conn, it, goal_of(gid), others)
                conn.execute("UPDATE items SET why=NULL WHERE id=?", (it["id"],))
            now = selfwork.store_item(conn, it["id"])
            live.append(now)
            taken.setdefault(str(r), []).append((now["id"], claims(now, r)))
            building[gid] = building.get(gid, 0) + 1
            waiting[gid] = [x for x in waiting[gid] if x["id"] != it["id"]]
            said.append(f"item {it['id']}: building ({it['title'][:50]})")
            started = True

    n = sum(building.values())
    for items in waiting.values():
        for it in items:
            why = blocker(it) or f"{n} building (self.parallel)"
            if why != it["why"]:
                conn.execute("UPDATE items SET why=? WHERE id=? AND state='waiting'", (why, it["id"]))
    return said


def claims(it: sqlite3.Row, repo: Optional[Path] = None) -> Set[str]:
    if repo is None:
        from . import selfwork
        repo = selfwork.repo()
    files = set(json.loads(it["files"] or "[]")) | set(json.loads(it["touched"] or "[]"))
    return expand(sorted(files), tracked(repo))


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
