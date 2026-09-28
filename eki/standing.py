"""Standing goals: a goal in words, tied to a git folder, worked on in rounds
(docs/design.md, "Standing goals and the budget").

The housekeeping pass calls `tick`. A round is an ordinary goal
(`selfwork.submit`, owner 'eki', source 'standing') whose plan run reads the
project and what earlier rounds did; the plan may answer `ITEMS: []`, and
then the standing goal rests. A folder that is eki's own source has project
NULL: its rounds are self goals like any other.

Nothing lives in memory. Before opening a round, `tick` settles the latest
one from the goals table. `rest_until` doubles as the mark of what is
settled: a round whose end is after it hasn't been counted yet, and settling
writes a rest_until at or past its end, all in one transaction — so a tick
repeated, or a restart mid-round, counts nothing twice. Opening a round
clears it (nothing of the new round is settled); one in the past means no rest.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import capacity, db, machine, projects, selfdraft, selfwork, store

#: a failed round rests the goal this long
FAIL_REST = 3600.0
#: failed rounds in a row that make it 'stuck' until `eki goal resume`
STUCK_AFTER = 3
#: earlier rounds shown to the next round's plan
LOOK_BACK = 3
#: a goal in one of these is a round still being planned
PLANNING = ("drafting", "planning")
#: an item in one of these is a round still being built
BUILDING = ("waiting", "building", "judging", "reviewing")
#: a round that ended like this counts as failed
FAILED = ("failed", "left")


# ---- in ---------------------------------------------------------------------------------------

def add(conn: sqlite3.Connection, folder: str, text: str, *, check: Optional[str] = None,
        branch: Optional[str] = None, issue: Optional[int] = None, project: Optional[str] = None) -> str:
    """A standing goal on `folder` (from GitHub issue `issue`, if any); ValueError when it
    isn't a git repo with a commit. With `project` (a projects.id) the folder isn't looked at."""
    text = (text or "").strip()
    if not text:
        raise ValueError("a standing goal needs words")
    if project is not None:
        pid = project
    else:
        pid = None if projects.is_self(folder) else projects.add(conn, folder, check=check, branch=branch)
    sid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO standing(id, project, text, state, created_at, issue) "
                     "VALUES (?,?,?,'on',?,?)", (sid, pid, text, db.now(), issue))
    return sid


def find(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    """The standing goal whose id starts with `sid`; KeyError if none or several."""
    rows = conn.execute("SELECT * FROM standing WHERE id LIKE ? ORDER BY created_at",
                        ((sid or "").strip() + "%",)).fetchall() if (sid or "").strip() else []
    if len(rows) != 1:
        raise KeyError(f"no standing goal {sid}" if not rows else f"{sid} is more than one standing goal")
    return rows[0]


def _update(conn: sqlite3.Connection, sid: str, **fields) -> str:
    st = find(conn, sid)
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx(conn):
        conn.execute(f"UPDATE standing SET {cols} WHERE id=?", (*fields.values(), st["id"]))
    return st["id"]


def pause(conn: sqlite3.Connection, sid: str) -> str:
    return _update(conn, sid, state="paused", why="paused")


def resume(conn: sqlite3.Connection, sid: str) -> str:
    """On again, with failures, rest and stuck cleared (rest_until=now keeps what is settled)."""
    return _update(conn, sid, state="on", failures=0, rest_until=db.now(), why=None)


def drop(conn: sqlite3.Connection, sid: str) -> str:
    return _update(conn, sid, state="dropped", why="dropped")


def now(conn: sqlite3.Connection, sid: str) -> str:
    """Clear the rest only: the Mac and the budget still decide."""
    st = find(conn, sid)
    t = db.now()
    with db.tx(conn):
        conn.execute("UPDATE standing SET rest_until=? WHERE id=? AND rest_until>?", (t, st["id"], t))
    return st["id"]


# ---- the tick ---------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection, now: Optional[float] = None) -> List[str]:
    t = db.now() if now is None else now
    said: List[str] = []
    for st in conn.execute("SELECT * FROM standing WHERE state='on' ORDER BY created_at").fetchall():
        if _open(conn, st["id"]):
            continue
        said += _settle(conn, st, t)
        st = conn.execute("SELECT * FROM standing WHERE id=?", (st["id"],)).fetchone()
        if st["state"] != "on" or (st["rest_until"] or 0) > t:
            continue
        why = _held(conn, st)
        if why:
            if why != st["why"]:
                with db.tx(conn):
                    conn.execute("UPDATE standing SET why=? WHERE id=?", (why, st["id"]))
            continue
        gid = selfwork.submit(conn, round_text(conn, st), plan=True, draft=False, owner="eki",
                              source_kind="standing", project=st["project"], standing_id=st["id"],
                              issue=st["issue"])
        with db.tx(conn):
            conn.execute("UPDATE standing SET rounds=(SELECT COUNT(*) FROM goals WHERE standing_id=?), "
                         "last_round_at=?, rest_until=NULL, why=NULL WHERE id=?", (st["id"], t, st["id"]))
        said.append(f"standing {st['id']}: round goal {gid} opened")
    return said


def _open(conn: sqlite3.Connection, sid: str) -> bool:
    goals = ",".join("?" * len(PLANNING))
    items = ",".join("?" * len(BUILDING))
    return conn.execute(
        f"SELECT 1 FROM goals g WHERE g.standing_id=? AND (g.state IN ({goals}) OR EXISTS "
        f"(SELECT 1 FROM items i WHERE i.goal_id=g.id AND i.state IN ({items}))) LIMIT 1",
        (sid, *PLANNING, *BUILDING)).fetchone() is not None


def _rounds(conn: sqlite3.Connection, sid: str, limit: Optional[int] = None) -> List[sqlite3.Row]:
    sql = "SELECT * FROM goals WHERE standing_id=? ORDER BY created_at DESC, rowid DESC"
    return conn.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), (sid,)).fetchall()


def _ended(conn: sqlite3.Connection, g: sqlite3.Row) -> float:
    """When a round was over: its plan run's end, or its last item's move."""
    run = store.run(conn, g["plan_run"]) if g["plan_run"] else None
    last = conn.execute("SELECT MAX(updated_at) FROM items WHERE goal_id=?", (g["id"],)).fetchone()[0]
    return max(g["created_at"], (run["ended_at"] if run else None) or 0, last or 0)


def _settle(conn: sqlite3.Connection, st: sqlite3.Row, t: float) -> List[str]:
    """Count the latest round once: failures, rest, stuck."""
    rounds = _rounds(conn, st["id"], 1)
    if not rounds:
        return []
    g = rounds[0]
    end = _ended(conn, g)
    with db.tx(conn):
        mark = conn.execute("SELECT rest_until FROM standing WHERE id=?", (st["id"],)).fetchone()[0]
        if mark is not None and mark >= end:
            return []                                   # counted already
        empty = g["state"] == "planned" and not conn.execute(
            "SELECT 1 FROM items WHERE goal_id=? LIMIT 1", (g["id"],)).fetchone()
        if g["state"] in FAILED:
            failures = st["failures"] + 1
            state = "stuck" if failures >= STUCK_AFTER else "on"
            why = (f"{failures} rounds failed in a row: `eki goal resume {st['id']}`" if state == "stuck"
                   else f"the last round {g['state']}: {(g['error'] or '').splitlines()[0][:160] if g['error'] else '?'}")
            conn.execute("UPDATE standing SET failures=?, state=?, rest_until=?, why=? WHERE id=?",
                         (failures, state, end + FAIL_REST, why, st["id"]))
            return [f"standing {st['id']}: round {g['id']} {g['state']} ({failures} in a row)"
                    + (" — stuck" if state == "stuck" else "")]
        rest = end + float(selfwork.settings()["standing_rest_hours"]) * 3600 if empty else max(end, t)
        why = f"nothing worth doing now: {g['error'] or '?'}" if empty else None
        conn.execute("UPDATE standing SET failures=0, rest_until=?, why=? WHERE id=?", (rest, why, st["id"]))
    return [f"standing {st['id']}: resting — {why}"] if empty else []


def _held(conn: sqlite3.Connection, st: sqlite3.Row) -> Optional[str]:
    """Why no round opens now, or None when one may."""
    ok, why = machine.room()
    if not ok:
        return f"waiting for the Mac: {why}"
    provider = selfdraft.planner()[0]
    ok, why = capacity.status(conn, background=True).get(provider, (False, "not configured"))
    if not ok:
        return f"waiting for {provider}: {why}"
    most = int(selfwork.settings()["standing_waiting_max"])
    waiting = _proposed(conn, st)
    if waiting >= most:
        return f"{waiting} proposed item{'s' if waiting != 1 else ''} waiting for you"
    return None


def _proposed(conn: sqlite3.Connection, st: sqlite3.Row) -> int:
    """The project's proposed items; for eki itself, those of this goal's rounds."""
    if st["project"] is None:
        sql, arg = "g.standing_id=?", st["id"]
    else:
        sql, arg = "g.project=?", st["project"]
    return conn.execute(f"SELECT COUNT(*) FROM items i JOIN goals g ON g.id=i.goal_id "
                        f"WHERE {sql} AND i.state='proposed'", (arg,)).fetchone()[0]


# ---- the round's goal -------------------------------------------------------------------------

def round_text(conn: sqlite3.Connection, st: sqlite3.Row) -> str:
    """The standing text, where it applies and what the last rounds did."""
    project = projects.get(conn, st["project"]) if st["project"] else None
    where = f"{project['name']} at {project['path']} (branch {project['branch']})" if project \
        else f"eki itself ({selfwork.source()})"
    out = [st["text"].strip(), "", f"This is the next round of a standing goal on {where}.",
           "Plan the next few items worth doing toward it now, reading the project as it is."]
    rounds = _rounds(conn, st["id"], LOOK_BACK)
    if rounds:
        out += ["", "What earlier rounds did, newest first:"]
    for g in rounds:
        items = conn.execute("SELECT title, state, summary, error FROM items WHERE goal_id=? "
                             "ORDER BY created_at", (g["id"],)).fetchall()
        head = f"- round {g['id']} ({g['state']})"
        if not items:
            out.append(head + (f": {_first(g['error'])}" if g["error"] else ": no items"))
            continue
        out.append(head + ":")
        for it in items:
            note = _first(it["summary"]) or _first(it["error"])
            out.append(f"  - {it['title']} — {it['state']}" + (f": {note}" if note else ""))
    out += ["", "Don't redo what an earlier round did or what waits to be merged. When nothing is worth "
                "doing now, the right answer is one line saying why, then `ITEMS: []`."]
    return "\n".join(out)


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")[:160]
