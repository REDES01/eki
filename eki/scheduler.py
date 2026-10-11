"""Starting items: which waiting items may build now (docs/self-build.md, "Items").

Waiting items are taken goal by goal, round-robin: the goal with the fewest
items building goes first, then the older goal, then the older item. An item
starts once its deps are fit, up to `self.parallel` building at once —
judging and review are held elsewhere. A project whose workflow card says
`parallel: n` (eki/workflow.py) has at most n of its items live (building,
judging, reviewing) besides; with no card nothing more caps it. Items whose write-sets overlap build
at the same time: the queue merges them (a resolve run on conflict). Only an
item with no write-set waits, until nothing else builds in its repo.
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

from . import db, projects, workflow, workspace

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

    caps: Dict[str, Optional[int]] = {}                  # project id -> its card's parallel

    def cap_of(pid: Optional[str]) -> Optional[int]:
        if pid is None:
            return None                                   # eki itself: self.parallel
        if pid not in caps:
            row = projects.get(conn, pid)
            wf = workflow.of_project(conn, row) if row is not None else None
            caps[pid] = wf.parallel if wf is not None else None
        return caps[pid]

    def project_of(gid: str) -> Optional[str]:
        g = goal_of(gid)
        return g["project"] if g is not None and "project" in g.keys() else None

    live = selfwork.items_in(conn, selfwork.LIVE)
    live_in: Dict[str, int] = {}                          # project id -> its items live
    for x in live:
        pid = project_of(x["goal_id"])
        if pid is not None:
            live_in[pid] = live_in.get(pid, 0) + 1
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
        pid = project_of(it["goal_id"])
        cap = cap_of(pid)
        if cap is not None and live_in.get(pid, 0) >= cap:
            return f"{live_in[pid]} live in its project (its workflow: parallel {cap})"
        if it["independent"]:
            return None
        files = json.loads(it["files"] or "[]")
        if files:       # a declared write-set never waits on another: overlaps are merged by the queue
            return None
        return NO_WRITE_SET if taken.get(str(r)) else None

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
            pid = project_of(gid)
            if pid is not None:
                live_in[pid] = live_in.get(pid, 0) + 1
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
