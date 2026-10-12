"""Where a goal's work happens: eki itself, the default project, or a person's
own (docs/work-model.md).

eki is a built-in value, not a `projects` row: its goals keep `project`
NULL. Self-work and a project differ only in their WorkProject (and the
executor that carries out its actions), never in the rules of
eki/workstep.py. `settings` is the one function that turns today's cards
and settings into the model's Settings.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import locks, projects, workflow, workflowgates
from .workmodel import Settings

EKI = "eki"


@dataclass(frozen=True)
class WorkProject:
    id: str                # "eki" or the project's id
    name: str
    repo: Path             # where its branches and worktrees live
    branch: str
    kind: str              # eki | local | github
    settings: Settings


def of_goal(conn: sqlite3.Connection, goal: Optional[sqlite3.Row]) -> WorkProject:
    """The goal's project; eki's own for a goal with no project. KeyError when its project is gone."""
    pid = goal["project"] if goal is not None and "project" in goal.keys() else None
    if not pid:
        from . import integration
        return WorkProject(EKI, EKI, integration.repo(), "main", EKI, settings(conn, None))
    row = projects.get(conn, pid)
    if row is None:
        raise KeyError(f"no project {pid}")
    return WorkProject(row["id"], row["name"], Path(row["path"]), row["branch"], kind(row), settings(conn, row))


def kind(row: sqlite3.Row) -> str:
    return "github" if "repo" in row.keys() and row["repo"] else "local"


def _plan(wf: Optional[workflow.Workflow]) -> str:
    return "single" if wf is not None and wf.plan == "single" else "on"


def settings(conn: Optional[sqlite3.Connection], row: Optional[sqlite3.Row]) -> Settings:
    """The model's settings for eki (row None: its own card and self settings) or a project row
    (its card, else the row's own check and autonomy)."""
    from . import selfwork          # selfwork imports half of eki: import it late
    st = selfwork.settings()
    if row is None:
        wf = workflowgates.own()
        gates = ("check",) + (("review",) if st.get("review", True) else ()) + ("batch",)
        return Settings(plan=_plan(wf), gates=gates, merge="auto" if st.get("autonomy") == "apply" else "person",
                        parallel=int(st.get("parallel") or 0), locked=tuple(locks.HARD_LOCKED), kind=EKI)
    wf = workflow.of_project(conn, row)
    merge = "auto" if projects.autonomy(row) == "apply" else "person"
    if wf is None:                  # no card: the row's own check, the self review setting
        has_check = bool(projects.legacy_check(row))
        gates = (("check",) + (("review",) if st.get("review", True) else ()) + ("batch",)) if has_check else ()
        return Settings(plan="on", gates=gates, merge=merge, parallel=0, locked=(), kind=kind(row))
    gates = tuple(g for g, on in (
        ("check", workflow.has(wf, "command") and bool(wf.command.strip() or wf.rules)),
        ("review", workflow.has(wf, "review")),
        ("playtester", workflow.has(wf, "playtester")),
        ("batch", workflow.has(wf, "batch") and projects.batch_argv(row) is not None)) if on)
    return Settings(plan=_plan(wf), gates=gates, merge=merge, parallel=int(wf.parallel or 0), locked=(),
                    kind=kind(row))
