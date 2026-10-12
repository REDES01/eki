"""The build cap in force for a project (docs/self-build.md, "The scheduler").

Two limits hold a project's items building: the machine's agents at once
(eki/machinecaps.py), shared by every project, and the project's own workflow
card `parallel` (eki/workflow.py). `project_cap` is the project's own ceiling as
the scheduler reads it; `of` says how many may build now, which limit is in
force, and the line the pages show. Nothing is kept: everything is read fresh.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from . import machinecaps, projectchats, projects, workflow, workflowgates


def _is_eki(pid: Optional[str]) -> bool:
    return pid is None or pid == projectchats.SELF


def project_cap(conn: sqlite3.Connection, pid: Optional[str]) -> Optional[int]:
    """The project's own ceiling: its workflow card's parallel; eki's falls back to its settings."""
    if _is_eki(pid):
        wf = workflowgates.own()
        if wf is not None:
            return wf.parallel
        from . import selfwork          # selfwork imports the scheduler, which imports this
        return int(selfwork.settings()["parallel"])
    row = projects.get(conn, pid)
    wf = workflow.of_project(conn, row) if row is not None else None
    return wf.parallel if wf is not None else None


def _building(conn: sqlite3.Connection, pid: Optional[str]) -> Dict[str, int]:
    rows = conn.execute("SELECT g.project AS project, COUNT(*) AS n FROM items i "
                        "JOIN goals g ON g.id = i.goal_id WHERE i.state='building' "
                        "GROUP BY g.project").fetchall()
    mine = None if _is_eki(pid) else pid
    here = sum(r["n"] for r in rows if r["project"] == mine)
    return {"building": here, "others": sum(r["n"] for r in rows) - here}


def of(conn: sqlite3.Connection, pid: Optional[str]) -> Dict[str, Any]:
    """building, others, machine, project, now, by ("machine" | "project") and the line."""
    counts = _building(conn, pid)
    building, others = counts["building"], counts["others"]
    machine = int(machinecaps.settings()["agents"])
    project = project_cap(conn, pid)
    room = max(0, machine - others)
    by = "machine" if project is None or room <= project else "project"
    now = room if by == "machine" else project
    if by == "machine":
        where = f"machine: {machine}, {others} in other projects" if others else "machine"
        rest = (f"this project allows {project}" if project is not None
                else "this project has no cap of its own")
        line = f"{building} building · up to {now} now ({where}) · {rest}"
    else:
        also = f" ({others} in other projects)" if others else ""
        line = f"{building} building · up to {now} now (this project) · the machine allows {machine}{also}"
    return {"building": building, "others": others, "machine": machine, "project": project,
            "now": now, "by": by, "line": line}
