"""The loop: when nothing is queued, eki picks its own next piece of self-work
(docs/self-build.md, "The loop: eki picks its own work").

Off until the person turns it on (`eki self loop on` writes routing.json
`self.loop`; nothing here ever does). The housekeeping pass calls `tick`,
which opens at most one goal, and only when eki is idle, fewer than
`self.review_max` results wait on the person and fewer than
`self.picks_per_day` goals were picked in the last 24 hours. The order:
faults first (eki/faults.py), then the journal's costliest cluster
(eki/costs.py), then the open ROADMAP.md entry a model ranks highest
(eki/roadmap.py, eki/selfdraft.py `open_rank`). Every goal it opens is owner
'eki' and carries a `why`. The goals table is all the state there is: a
restart loses nothing, and the goal a pick opens makes eki busy, so picks
happen one at a time.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import costs, db, faults, paths, roadmap, selfdraft, selfwork

DAY = 86400.0
WINDOW = 7 * DAY
#: goals in these states have a draft or plan run going
GOAL_BUSY = ("drafting", "planning")
#: items in these states are work under way or about to be
ITEM_BUSY = ("waiting", "building", "judging", "reviewing", "queued", "resolving", "rechecking")
#: items in these states wait on the person
ITEM_WAITING = ("proposed", "locked")
#: a `now` run in one of these is the person's work under way
RUN_BUSY = ("queued", "running")


@dataclass
class Pick:
    stage: str                  # fault | journal | roadmap
    why: str
    pick_key: Optional[str]     # None for roadmap: the ranking sets it
    payload: Any


def settings() -> Dict[str, Any]:
    s = selfwork.settings()
    return {"loop": bool(s.get("loop")), "review_max": int(s.get("review_max", 3)),
            "picks_per_day": int(s.get("picks_per_day", 6)),
            "pick_min_cluster": int(s.get("pick_min_cluster", 3))}


def set_loop(on: bool) -> None:
    """routing.json `self.loop`. Only the `eki self loop` verb calls this."""
    path = paths.config("routing")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data["self"] = {**(data.get("self") or {}), "loop": bool(on)}
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _marks(n: int) -> str:
    return ",".join("?" * n)


def idle(conn: sqlite3.Connection) -> Tuple[bool, str]:
    """(True, "nothing queued"), or (False, what keeps eki busy)."""
    g = conn.execute(f"SELECT id, state FROM goals WHERE state IN ({_marks(len(GOAL_BUSY))})"
                     " ORDER BY created_at LIMIT 1", GOAL_BUSY).fetchone()
    if g is not None:
        return False, f"goal {g['id']} is {g['state']}"
    i = conn.execute(f"SELECT id, state FROM items WHERE state IN ({_marks(len(ITEM_BUSY))})"
                     " ORDER BY created_at LIMIT 1", ITEM_BUSY).fetchone()
    if i is not None:
        return False, f"item {i['id']} is {i['state']}"
    r = conn.execute(f"SELECT id, state FROM runs WHERE priority='now' AND state IN ({_marks(len(RUN_BUSY))})"
                     " ORDER BY created_at LIMIT 1", RUN_BUSY).fetchone()
    if r is not None:
        return False, f"run {r['id']} (now) is {r['state']}"
    return True, "nothing queued"


def waiting(conn: sqlite3.Connection) -> int:
    """Results waiting on the person: proposed or locked items, and open asks
    on the threads of goals and items."""
    items = conn.execute(f"SELECT COUNT(*) FROM items WHERE state IN ({_marks(len(ITEM_WAITING))})",
                         ITEM_WAITING).fetchone()[0]
    asks = conn.execute(
        "SELECT COUNT(*) FROM asks WHERE state='open' AND ("
        " thread_id IN (SELECT thread_id FROM goals WHERE thread_id IS NOT NULL)"
        " OR thread_id IN (SELECT thread_id FROM items WHERE thread_id IS NOT NULL))").fetchone()[0]
    return int(items) + int(asks)


def picked_today(conn: sqlite3.Connection, now: Optional[float] = None) -> int:
    """Goals picked in the last 24 hours. A ranking goal has no pick_key until
    its draft concludes, so roadmap goals count by their source."""
    now = db.now() if now is None else now
    return conn.execute("SELECT COUNT(*) FROM goals WHERE (pick_key IS NOT NULL OR source='roadmap')"
                        " AND created_at>=?", (now - DAY,)).fetchone()[0]


def choose(conn: sqlite3.Connection, now: Optional[float] = None) -> Optional[Pick]:
    """What would be picked now, whatever the switch, idleness or caps say. Writes nothing."""
    now = db.now() if now is None else now
    if faults.room(conn, now):
        found = faults.pending(conn, now - WINDOW, now)
        if found:
            k, seen = found[0]
            return Pick("fault", f"fault {k}, seen {len(seen)} times in 7 days", f"fault:{k}", (k, seen))
    clusters = costs.clusters(conn, now - WINDOW, now)
    if clusters:
        c = clusters[0]
        why = f"{c.count} {c.kind}s on row {c.row} in 7 days"
        if c.kind == "handoff":
            why += " that could stay local"
        return Pick("journal", why, f"journal:{c.key}", c)
    entries = roadmap.eligible(conn, roadmap.open_entries(roadmap.read_main()))
    if entries:
        return Pick("roadmap", f"{len(entries)} open ROADMAP.md entries to rank", None, entries)
    return None


def stopped(conn: sqlite3.Connection, now: Optional[float] = None) -> Optional[str]:
    """Why the loop would not pick now (switch, idleness, waiting, the daily cap), or None."""
    s = settings()
    if not s["loop"]:
        return "the loop is off"
    ok, why = idle(conn)
    if not ok:
        return f"busy: {why}"
    n = waiting(conn)
    if n >= s["review_max"]:
        return f"{n} wait for you (review_max {s['review_max']})"
    if picked_today(conn, now) >= s["picks_per_day"]:
        return f"{s['picks_per_day']} picks in the last 24 hours (picks_per_day)"
    return None


def tick(conn: sqlite3.Connection) -> List[str]:
    """Open at most one goal. Exceptions propagate: housekeeping writes them down as a fault."""
    now = db.now()
    if stopped(conn, now) is not None:
        return []
    p = choose(conn, now)
    if p is None:
        return []
    if p.stage == "fault":
        k, found = p.payload
        gid = faults.open_goal(conn, k, found, p.why)
    elif p.stage == "journal":
        gid = selfwork.submit(conn, costs.goal_text(p.payload), plan=True, draft=False, owner="eki",
                              source_kind="journal", why=p.why, pick_key=p.pick_key)
    else:
        gid = selfdraft.open_rank(conn, p.payload, p.why)
    return [f"pick: goal {gid} — {p.why}"]


def line(conn: sqlite3.Connection) -> str:
    """The board's loop line."""
    s = settings()
    if not s["loop"]:
        return "loop: off (eki self loop on)"
    n = waiting(conn)
    if n >= s["review_max"]:
        return f"loop: stopped — {n} wait for you (eki self apply …)"
    ok, why = idle(conn)
    if not ok:
        return f"loop: on — busy: {why}"
    if picked_today(conn) >= s["picks_per_day"]:
        return f"loop: on — {s['picks_per_day']} picks today, the most a day allows"
    return "loop: on — idle, picks next pass"
