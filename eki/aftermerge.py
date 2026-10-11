"""The workflow card's `after_merge`: a build in the person's folder after eki lands there.

A local project whose card says merge auto and names an `after_merge` command
(e.g. `npm run build`) gets one provider='command' run of it in the folder
where its branch is checked out (projectland.checked_out), after items land
there (projectqueue sets `landed_on`). The landing is never blocked or undone:
this only follows it.

One run per project at a time. While it runs nothing new starts; when it ends
and items landed after it started, exactly one more run covers all of them,
so several landings close together cost one build after the last.

Nothing is kept in memory. EKI_HOME/aftermerge/<project id>.json says

    {"run": rid, "items": [ids], "upto": landed_at, "from": landed_at,
     "state": "running"|"done"|"failed", "error": tail|None, "at": float}

where `upto` is the newest landed_at the run covers and `from` the one before
it. Each run's thread is titled "after merge <project id>: <name>", so a kill
between creating the run and writing the file finds the run again instead of
starting a second one.

A failed run sets each covered item's why to "after-merge build failed: <tail>"
(the board and the goal show it through selfview.said) and queues one
needs-you notice; a later run that passes clears it.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, notify, paths, projectland, store, workflow
from .workspace import WorkspaceError

log = logging.getLogger("eki.aftermerge")

FAILED = "after-merge build failed"
#: about this much of the output's end is kept as the tail
TAIL_CHARS = 300
TAIL_LINES = 6


def _path(pid: str) -> Path:
    return paths.home() / "aftermerge" / f"{pid}.json"


def status(pid: str) -> Optional[Dict[str, Any]]:
    try:
        got = json.loads(_path(pid).read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _write(pid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    rec = {**rec, "at": time.time()}
    where = _path(pid)
    where.parent.mkdir(parents=True, exist_ok=True)
    tmp = where.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, where)
    return rec


def _title(project: sqlite3.Row) -> str:
    return f"after merge {project['id']}: {project['name']}"


def _projects(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects WHERE repo IS NULL AND branch IS NOT NULL "
                        "AND COALESCE(state, 'on') NOT IN ('deleting', 'deleted') ORDER BY created_at").fetchall()


def _landed(conn: sqlite3.Connection, pid: str, after: float) -> List[sqlite3.Row]:
    """The project's items eki landed on its branch after `after` (a landed_at)."""
    return conn.execute("SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? "
                        "AND i.state='applied' AND i.landed_on IS NOT NULL AND i.landed_at > ? "
                        "ORDER BY i.landed_at", (pid, after)).fetchall()


def tail(conn: sqlite3.Connection, run: sqlite3.Row) -> str:
    """The last few lines the run said (its error when it said nothing), about TAIL_CHARS long."""
    text = store.answer(conn, run["id"]).strip() or (run["error"] or "").strip()
    text = "\n".join(text.splitlines()[-TAIL_LINES:])
    if not text:
        text = f"the build {run['state']}"
    return text[-TAIL_CHARS:]


# ---- the tick --------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    for project in _projects(conn):
        wf = workflow.of_project(conn, project)
        if wf is None or wf.merge != "auto" or not wf.after_merge.strip():
            continue
        try:
            said += _one(conn, project, wf.after_merge)
        except (WorkspaceError, OSError) as e:          # one project's trouble never stops the others
            log.warning("after-merge: %s: %s", project["name"], e)
    return said


def _one(conn: sqlite3.Connection, project: sqlite3.Row, cmd: str) -> List[str]:
    pid = project["id"]
    rec = status(pid) or {}
    rec, said = _adopt(conn, project, rec)
    if rec.get("state") == "running":
        run = store.run(conn, rec["run"]) if rec.get("run") else None
        if run is not None and run["state"] in store.ACTIVE:
            return said
        if run is None or run["state"] not in store.ENDED:
            rec = _write(pid, {**rec, "state": "gone", "upto": rec.get("from") or 0.0})   # its items again
        else:
            rec, lines = _settle(conn, project, rec, run)
            said += lines
    pending = _landed(conn, pid, float(rec.get("upto") or 0.0))
    if not pending:
        return said
    where = projectland.checked_out(project["path"], project["branch"])
    if where is None:
        return said                                     # nowhere to build; tried again after the next landing
    return said + _start(conn, project, rec, pending, where, cmd)


def _adopt(conn: sqlite3.Connection, project: sqlite3.Row, rec: Dict[str, Any]) -> tuple:
    """A run made for this project that the file doesn't know (a kill before it was written)."""
    pid = project["id"]
    row = conn.execute("SELECT r.* FROM runs r JOIN threads t ON t.id=r.thread_id WHERE t.by='eki' "
                       "AND t.title LIKE ? AND r.provider='command' ORDER BY r.created_at DESC LIMIT 1",
                       (f"after merge {pid}:%",)).fetchone()
    if row is None or row["id"] == rec.get("run"):
        return rec, []
    known = store.run(conn, rec["run"]) if rec.get("run") else None
    if known is not None and known["created_at"] >= row["created_at"]:
        return rec, []
    upto = float(rec.get("upto") or 0.0)
    covered = [r for r in _landed(conn, pid, upto) if r["landed_at"] <= row["created_at"]]
    rec = _write(pid, {"run": row["id"], "items": [r["id"] for r in covered], "from": upto,
                       "upto": max([upto] + [r["landed_at"] for r in covered]), "state": "running", "error": None})
    return rec, [f"after-merge: found run {row['id'][:8]} again"]


def _start(conn: sqlite3.Connection, project: sqlite3.Row, rec: Dict[str, Any], pending: List[sqlite3.Row],
           where: Path, cmd: str) -> List[str]:
    upto = float(rec.get("upto") or 0.0)
    with db.tx(conn):
        tid = store.create_thread(conn, _title(project), str(where), by="eki")
        rid = store.create_run(conn, tid, json.dumps(["/bin/sh", "-c", cmd]), provider="command",
                               priority="background")
    _write(project["id"], {"run": rid, "items": [r["id"] for r in pending], "from": upto,
                           "upto": max(r["landed_at"] for r in pending), "state": "running", "error": None})
    n = len(pending)
    return [f"after-merge: {project['name']}: `{cmd}` in {where} for {n} landed item{'s' if n != 1 else ''} "
            f"({rid[:8]})"]


def _settle(conn: sqlite3.Connection, project: sqlite3.Row, rec: Dict[str, Any],
            run: sqlite3.Row) -> tuple:
    ids = [str(i) for i in rec.get("items") or []]
    marks = ",".join("?" * len(ids))
    if run["state"] == "done":
        if ids:
            with db.tx(conn):
                conn.execute(f"UPDATE items SET why=NULL WHERE id IN ({marks}) AND why LIKE ?",
                             (*ids, FAILED + "%"))
        return _write(project["id"], {**rec, "state": "done", "error": None}), \
            [f"after-merge: {project['name']}: built ({run['id'][:8]})"]
    end = tail(conn, run)
    why = f"{FAILED}: {end}"
    with db.tx(conn):
        if ids:
            conn.execute(f"UPDATE items SET why=? WHERE id IN ({marks}) AND state='applied'", (why, *ids))
        notify.queue(conn, f"aftermerge:{run['id']}", "needs_you", "eki · needs you",
                     f"{project['name']}: landed; {why}")
    return _write(project["id"], {**rec, "state": "failed", "error": end}), \
        [f"after-merge: {project['name']}: build {run['state']} ({run['id'][:8]})"]
