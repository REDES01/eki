"""The dry build at a goal's plan start: the project's check run once in a scratch
worktree, so the goal's conclusion can see which tracked files a build rewrites
(eki/hotspots.py). Only when the check builds; never in the plan's way — any
error is logged and the plan goes on without it. The conclusion removes the
worktree."""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from . import hotspots, projects, store, workspace

log = logging.getLogger("eki.drybuild")


def key(gid: str) -> str:
    return f"dry-{gid}"


def command(project: sqlite3.Row) -> Optional[str]:
    """The check's command line for no touched files: the workflow card's, else the row's."""
    from . import workflow
    card = workflow.of_project(None, project)
    return workflow.check_line(card, []) if card is not None else projects.legacy_check(project)


def start(conn: sqlite3.Connection, gid: str, project: Optional[sqlite3.Row], base: str,
          text: str, priority: str) -> Optional[str]:
    """Start goal `gid`'s dry build (inside the caller's tx) and return its run id; None when
    the goal is eki's own, the project has no check, or the check doesn't build."""
    if project is None:
        return None
    repo = wt = None
    conn.execute("SAVEPOINT drybuild")
    try:
        argv = projects.check_argv(project)
        if argv is None or not hotspots.builds(command(project)):
            conn.execute("RELEASE drybuild")
            return None
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
        repo = projects.repo_for(conn, goal)
        wt = workspace.add(repo, key(gid), base=base, branch=f"eki/{key(gid)}")
        projects.link_deps(project, wt)
        tid = store.create_thread(conn, f"dry build: {text}", str(wt), by="eki")
        rid = store.create_run(conn, tid, argv, provider="command", priority=priority)
        conn.execute("UPDATE goals SET dry_run=? WHERE id=?", (rid, gid))
        conn.execute("RELEASE drybuild")
        return rid
    except Exception:
        log.exception("goal %s: no dry build", gid)
        conn.execute("ROLLBACK TO drybuild")
        conn.execute("RELEASE drybuild")
        if repo is not None and wt is not None:
            try:
                workspace.remove(repo, key(gid), delete_branch=True)
            except Exception:
                log.exception("goal %s: the dry worktree stays", gid)
        return None
