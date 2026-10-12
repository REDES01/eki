"""Starting items: which waiting items may build now (docs/self-build.md, "Items").

Waiting items are taken goal by goal, round-robin: the goal with the fewest
items building goes first, then the older goal, then the older item. An item
starts once its deps are fit (the work model's rule, workstep.blocked_by: in
main, whoever put it there) and two caps allow it; starting it is the model's
item_started event (eki/buildfeed.py). The machine cap
(eki/machinecaps.py, routing.json machine.agents) holds the items building
across all projects, eki's and every person's — judging and review are held
elsewhere; it is checked first and wins. Then each project's own workflow card
`parallel: n` (eki/workflow.py) caps only that project's items building
(judging and review are held by the test-run slots, not by this cap): eki's own card for eki's items (selfwork.settings()'s
`parallel` when eki has no card), a project with no card has no cap of its own.
eki's card never caps another project. Items whose write-sets overlap build
at the same time: the queue merges them (a resolve run on conflict). Only an
item with no write-set waits, until nothing else builds in its repo.
Under both caps, an item starts only when the Mac's memory has room for one
more agent (eki/memadmit.py: one reading per pass, each start counted); one
held only by memory says 'waiting for memory (x GB free, swap y%)'. Building
items are never stopped.
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

from . import buildcap, machinecaps, memadmit, projects, workrun, workspace, workstep

NO_WRITE_SET = "no write-set: waits until its repo is quiet"


def start_ready(conn: sqlite3.Connection) -> List[str]:
    from . import buildfeed, selfwork   # selfwork calls this from its tick: import them late
    _tracked.clear()
    agents = int(machinecaps.settings()["agents"])
    goals: Dict[str, Optional[sqlite3.Row]] = {}
    states: Dict[str, tuple] = {}                        # goal id -> its items as the work model sees them

    def dep_of(it: sqlite3.Row) -> Optional[str]:
        """The first dependency not yet fit (workstep.blocked_by), or None."""
        if it["goal_id"] not in states:
            try:
                states[it["goal_id"]] = tuple(workrun.load(conn, it["goal_id"])[1])
            except KeyError:                              # its project is gone: said below
                states[it["goal_id"]] = ()
        items = states[it["goal_id"]]
        me = next((x for x in items if x.id == it["id"]), None)
        return workstep.blocked_by(items, me) if me is not None else None
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

    caps: Dict[Optional[str], Tuple[str, Optional[int]]] = {}   # project id -> (its name, its card's parallel)

    def cap_of(pid: Optional[str]) -> Tuple[str, Optional[int]]:
        if pid not in caps:
            row = projects.get(conn, pid) if pid is not None else None
            name = "eki" if pid is None else (row["name"] if row is not None else pid)
            caps[pid] = (name, buildcap.project_cap(conn, pid))
        return caps[pid]

    def project_of(gid: str) -> Optional[str]:
        g = goal_of(gid)
        return g["project"] if g is not None and "project" in g.keys() else None

    live = selfwork.items_in(conn, selfwork.LIVE)
    live_in: Dict[Optional[str], int] = {}                # project id (None: eki) -> its items live
    for x in live:
        if x["state"] != "building":                      # judging/review are held by the test-run slots
            continue
        pid = project_of(x["goal_id"])
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
        missing = dep_of(it)
        if missing:
            dep = conn.execute("SELECT title FROM items WHERE id=?", (missing,)).fetchone()
            return f"after {dep['title'] if dep else missing}"
        r = repo_of(it["goal_id"])
        if r is None:
            return "its project is gone"
        n = sum(building.values())
        if n >= agents:
            return f"{n} agents working (machine)"
        pid = project_of(it["goal_id"])
        name, cap = cap_of(pid)
        if cap is not None and live_in.get(pid, 0) >= cap:
            return f"{live_in[pid]} building in {name} (its workflow)"
        if it["independent"]:
            return None
        files = json.loads(it["files"] or "[]")
        if files:       # a declared write-set never waits on another: overlaps are merged by the queue
            return None
        return NO_WRITE_SET if taken.get(str(r)) else None

    said = []
    mem: Optional[memadmit.Tick] = None             # read only when something could start

    def memory() -> Optional[str]:
        nonlocal mem
        mem = mem or memadmit.Tick(conn)
        return mem.fits("agent")

    started = True
    while started and sum(building.values()) < agents:
        started = False
        order = sorted((g for g in waiting if waiting[g] and goal_of(g) is not None),
                       key=lambda g: (building.get(g, 0), goal_of(g)["created_at"], g))
        for gid in order:
            if sum(building.values()) >= agents:
                break
            it = next((x for x in waiting[gid] if blocker(x) is None), None)
            if it is None:
                continue
            if memory() is not None:
                break
            r = repo_of(gid)
            if not buildfeed.start(conn, it):           # the model said no: never counted
                waiting[gid] = [x for x in waiting[gid] if x["id"] != it["id"]]
                started = True
                continue
            mem.took("agent")
            now = selfwork.store_item(conn, it["id"])
            live.append(now)
            taken.setdefault(str(r), []).append((now["id"], claims(now, r)))
            building[gid] = building.get(gid, 0) + 1
            pid = project_of(gid)
            live_in[pid] = live_in.get(pid, 0) + 1
            waiting[gid] = [x for x in waiting[gid] if x["id"] != it["id"]]
            said.append(f"item {it['id']}: building ({it['title'][:50]})")
            started = True

    n = sum(building.values())
    for items in waiting.values():
        for it in items:
            why = blocker(it) or (n < agents and memory()) or f"{n} agents working (machine)"
            if why != it["why"]:
                conn.execute("UPDATE items SET why=? WHERE id=? AND state='waiting'", (why, it["id"]))
    return said + (workrun.carry(conn) if said else [])       # the builds start now, briefed on each other


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
