"""Issues in: a watched project's open GitHub issues labelled eki become its goals.

Each housekeeping pass (at most every self.issues_minutes) lists the issues
of every watched project on the GitHub path (eki/github.py). An issue by the
person gh is logged in as, not yet taken, becomes a background goal owned by
eki — or, labelled `standing`, a standing goal. Only the person's words count:
an issue by anyone else is skipped, and the log line says so. An edit to an
issue already taken is not followed. An issue closed on GitHub drops the
unfinished work it started; what is already proposed is left for the person.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Set, Tuple

from . import db, github, selfwork, standing, store

UNFINISHED = ("waiting", "building", "judging", "reviewing")
PLANNING = ("drafting", "planning")


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("issues"):
        return []
    said: List[str] = []
    me: Optional[str] = None
    for project in conn.execute("SELECT * FROM projects WHERE issues=1 ORDER BY created_at").fetchall():
        repo, why = github.path_of(project["path"])
        if repo is None:
            said.append(f"{project['name']}: not watching issues: {why}")
            continue
        try:
            me = me or github.whoami()
            said += _project(conn, project, repo, me)
        except github.GhError as e:
            said.append(f"{project['name']}: GitHub: {str(e).splitlines()[0] if str(e) else 'failed'}")
    return said


def text_of(issue: dict, repo: str) -> str:
    n = issue["number"]
    return f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}\n\n(GitHub issue #{n} on {repo})"


def _taken(conn: sqlite3.Connection, pid: str, n: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM goals WHERE project=? AND issue=? UNION ALL "
                             "SELECT 1 FROM standing WHERE project=? AND issue=?", (pid, n, pid, n)).fetchone())


def _project(conn: sqlite3.Connection, project: sqlite3.Row, repo: str, me: str) -> List[str]:
    said: List[str] = []
    name, pid = project["name"], project["id"]
    listed = github.issues(repo)
    open_numbers: Set[int] = {int(i["number"]) for i in listed}
    for issue in listed:
        n = int(issue["number"])
        if _taken(conn, pid, n):
            continue
        login = (issue.get("author") or {}).get("login") or ""
        if login != me:
            said.append(f"{name}: issue #{n} skipped — by @{login or 'unknown'}, not {me}; "
                        "only the person's words count")
            continue
        labels = {(lb.get("name") or "").lower() for lb in issue.get("labels") or []}
        text = text_of(issue, repo)
        if "standing" in labels:
            sid = standing.add(conn, project["path"], text, issue=n)
            said.append(f"{name}: issue #{n} became standing goal {sid}")
        else:
            gid = selfwork.submit(conn, text, plan=True, draft=False, owner="eki", source_kind="issue",
                                  project=pid, issue=n)
            said.append(f"{name}: issue #{n} became goal {gid}")
    return said + _closed(conn, project, repo, open_numbers)


def _closed(conn: sqlite3.Connection, project: sqlite3.Row, repo: str, open_numbers: Set[int]) -> List[str]:
    said: List[str] = []
    name, pid = project["name"], project["id"]
    goals = conn.execute("SELECT * FROM goals WHERE project=? AND issue IS NOT NULL AND standing_id IS NULL "
                         "AND state NOT IN ('left','failed')", (pid,)).fetchall()
    stands = conn.execute("SELECT * FROM standing WHERE project=? AND issue IS NOT NULL AND state!='dropped'",
                          (pid,)).fetchall()
    for g in goals:
        n = g["issue"]
        if n in open_numbers or not _live(conn, g) or github.issue_state(repo, n) != "CLOSED":
            continue
        dropped, failed = _stop(conn, g, n)
        said += failed
        said.append(f"{name}: issue #{n} closed — goal {g['id']} "
                    f"{'stopped' if not failed else 'kept open until its items drop'}, {dropped} item(s) dropped")
    for st in stands:
        n = st["issue"]
        if n in open_numbers or github.issue_state(repo, n) != "CLOSED":
            continue
        dropped, failed = 0, []
        for g in conn.execute("SELECT * FROM goals WHERE standing_id=? AND state NOT IN ('left','failed')",
                              (st["id"],)).fetchall():
            d, f = _stop(conn, g, n)
            dropped, failed = dropped + d, failed + f
        said += failed
        if failed:                  # still on, so the next pass looks again; its open round holds new ones
            said.append(f"{name}: issue #{n} closed — standing goal {st['id']} kept until its items drop")
            continue
        standing.drop(conn, st["id"])
        said.append(f"{name}: issue #{n} closed — standing goal {st['id']} dropped, {dropped} item(s) dropped")
    return said


def _live(conn: sqlite3.Connection, g: sqlite3.Row) -> bool:
    """Whether closing its issue still has something to stop: planning, or unfinished items."""
    if g["state"] in PLANNING:
        return True
    marks = ",".join("?" * len(UNFINISHED))
    return bool(conn.execute(f"SELECT 1 FROM items WHERE goal_id=? AND state IN ({marks})",
                             (g["id"], *UNFINISHED)).fetchone())


def _stop(conn: sqlite3.Connection, g: sqlite3.Row, n: int) -> Tuple[int, List[str]]:
    """Drop a goal's unfinished items, then cancel it if still planning; proposed ones are the person's.

    The goal is marked left only once every item has dropped: a drop that fails
    leaves the goal as it was, so the next pass finds it and tries again."""
    marks = ",".join("?" * len(UNFINISHED))
    items = conn.execute(f"SELECT id FROM items WHERE goal_id=? AND state IN ({marks})",
                         (g["id"], *UNFINISHED)).fetchall()
    dropped, failed = 0, []
    for it in items:
        try:
            selfwork.drop(conn, it["id"])
            dropped += 1
        except Exception as e:                           # noqa: BLE001 — one item's trouble stops one item
            failed.append(f"item {it['id']}: couldn't drop for issue #{n}: {str(e)[:120]}")
    if not failed and g["state"] in PLANNING:
        for rid in (g["draft_run"], g["plan_run"]):
            if rid:
                store.cancel(conn, rid)
        with db.tx(conn):
            conn.execute("UPDATE goals SET state='left', error=? WHERE id=?", (f"issue #{n} closed", g["id"]))
    return dropped, failed
