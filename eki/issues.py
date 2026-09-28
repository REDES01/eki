"""Issues in: an open issue labelled eki in any repo the person owns becomes a goal.

Nothing is registered. Each housekeeping pass (at most every
self.issues_minutes) asks gh once who is logged in and once for every open
issue labelled eki in the repos that login owns (eki/github.py). A repo with
no project yet gets one on the spot — eki's own clone (eki/projectsetup.py) —
and its issues wait, said on each pass, until that clone is `ready`; the pass
that turns it ready calls github.soon, so the next pass takes them. An issue
by the person, not yet taken on any row of its repo (retired ones included),
becomes a background goal owned by eki — or, labelled `standing`, a standing
goal. Only the person's words count: an issue by anyone else is skipped, and
the log line says so. A dropped project ignores the issues open when it was
dropped; a newer one revives it. An edit to an issue already taken is not
followed. An issue closed on GitHub drops the unfinished work it started;
what is already proposed is left for the person.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Set, Tuple

from . import db, github, projectsetup, projects, selfwork, standing, store

UNFINISHED = ("waiting", "building", "judging", "reviewing")
PLANNING = ("drafting", "planning")


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("issues"):
        return []
    try:
        me = github.whoami()
        found = github.search_issues(me)
    except github.GhError as e:
        return [f"GitHub: {_first(str(e))}"]
    by_repo: Dict[str, List[dict]] = {}
    for issue in found:
        repo = (issue.get("repository") or {}).get("nameWithOwner")
        if repo:
            by_repo.setdefault(repo, []).append(issue)
    said: List[str] = []
    for repo, listed in by_repo.items():
        said += _repo(conn, repo, listed, me)
    for project in conn.execute("SELECT * FROM projects WHERE repo IS NOT NULL "
                                "AND COALESCE(state,'on')!='dropped' ORDER BY created_at").fetchall():
        open_numbers = {int(i["number"]) for i in by_repo.get(project["repo"], [])}
        try:
            said += _closed(conn, project, project["repo"], open_numbers)
        except github.GhError as e:
            said.append(f"{project['name']}: GitHub: {_first(str(e))}")
    return said


def text_of(issue: dict, repo: str) -> str:
    n = issue["number"]
    return f"{issue.get('title') or ''}\n\n{issue.get('body') or ''}\n\n(GitHub issue #{n} on {repo})"


def _taken(conn: sqlite3.Connection, repo: str, n: int) -> bool:
    """Taken by any row of the repo, retired and dropped ones included: a moved project never takes it twice."""
    return bool(conn.execute("SELECT 1 FROM goals g JOIN projects p ON p.id=g.project WHERE p.repo=? AND g.issue=? "
                             "UNION ALL SELECT 1 FROM standing s JOIN projects p ON p.id=s.project "
                             "WHERE p.repo=? AND s.issue=?", (repo, n, repo, n)).fetchone())


def _ignored(row: sqlite3.Row) -> Set[int]:
    try:
        return {int(n) for n in json.loads(row["ignored"] or "[]")}
    except (ValueError, TypeError):
        return set()


def _waits(row: sqlite3.Row) -> str:
    if row["setup"] == "fault":
        return f"fault: {row['fault'] or 'unknown'}"
    return row["setup"] or "not set up"


def _repo(conn: sqlite3.Connection, repo: str, listed: List[dict], me: str) -> List[str]:
    said: List[str] = []
    row = projects.by_repo(conn, repo)
    for issue in listed:
        n = int(issue["number"])
        if _taken(conn, repo, n):
            continue
        dropped = row is not None and row["state"] == "dropped"
        if dropped and n in _ignored(row):
            continue
        login = (issue.get("author") or {}).get("login") or ""
        if login != me:
            said.append(f"{repo}: issue #{n} skipped — by @{login or 'unknown'}, not {me}; "
                        "only the person's words count")
            continue
        if row is None or dropped:
            row = projectsetup.ensure(conn, repo)
            said.append(f"{repo}: {'revived' if dropped else 'new project'} for issue #{n} — "
                        f"cloning into {row['path']}")
        if row["setup"] != "ready":
            said.append(f"{repo}: issue #{n} waits — {_waits(row)}")
            continue
        labels = {(lb.get("name") or "").lower() for lb in issue.get("labels") or []}
        text = text_of(issue, repo)
        if "standing" in labels:
            sid = standing.add(conn, row["path"], text, project=row["id"], issue=n)
            said.append(f"{repo}: issue #{n} became standing goal {sid}")
        else:
            gid = selfwork.submit(conn, text, plan=True, draft=False, owner="eki", source_kind="issue",
                                  project=row["id"], issue=n)
            said.append(f"{repo}: issue #{n} became goal {gid}")
    return said


def _first(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "") or "failed"


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
