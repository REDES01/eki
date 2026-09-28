"""Pull requests out: a proposed item on a GitHub-path project goes back as a PR.

Each housekeeping pass (at most every self.issues_minutes) pushes eki/<id>
to origin and opens (or finds) its pull request, then polls the open ones:
merged on GitHub is applied, closed without merging is dropped, and an item
the person dropped in eki has its PR closed. A project that is on the local
path this pass (origin not on GitHub, gh missing or logged out) is left
alone — no gh runs for it, and its open PRs wait until gh is back.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import db, github, projects, projectwork, selfwork, workspace

#: how much of the check's output goes into the PR body
TAIL = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _items(conn: sqlite3.Connection, where: str) -> List[sqlite3.Row]:
    """Project items matching `where`, with their goal's project and issue."""
    return conn.execute(
        "SELECT i.*, g.project AS project_id, g.issue AS goal_issue, g.standing_id AS goal_standing "
        f"FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project IS NOT NULL AND {where} "
        "ORDER BY i.created_at").fetchall()


def body(conn: sqlite3.Connection, it: sqlite3.Row) -> str:
    """The PR body: summary, touched files, the check's tail, the issue line, eki's mark."""
    parts = [(it["summary"] or it["title"]).strip()]
    touched = json.loads(it["touched"] or "[]")
    if touched:
        parts.append("Touched:\n" + "\n".join(f"- `{f}`" for f in touched))
    verdict = (it["verdict"] or "").strip()
    if not verdict or verdict == projectwork.NOT_JUDGED:
        parts.append(f"Check: {projectwork.NOT_JUDGED}")
    else:
        tail = "\n".join(verdict.splitlines()[-TAIL:])
        parts.append(f"Check:\n```\n{tail}\n```")
    n = it["goal_issue"]
    if n:
        others = conn.execute("SELECT state, pr FROM items WHERE goal_id=? AND id<>?",
                              (it["goal_id"], it["id"])).fetchall()
        last = all(o["state"] == "applied" or (o["state"] == "proposed" and o["pr"]) for o in others)
        parts.append(f"Closes #{n}" if last and not it["goal_standing"] else f"Part of #{n}")
    parts.append(github.MARK)
    return "\n\n".join(parts)


def _open(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row, repo: str) -> str:
    branch = f"eki/{it['id']}"
    where = it["worktree"] if it["worktree"] and Path(it["worktree"]).is_dir() else project["path"]
    github.push(where, f"refs/heads/{branch}", branch)
    found = github.pr_find(repo, branch)
    url = found["url"] if found and found.get("url") else None
    if url is None:
        url = github.pr_create(repo, base=project["branch"], branch=branch, title=it["title"],
                               body=body(conn, it))
    with db.tx(conn):
        selfwork._set(conn, it["id"], pr=url, pr_state="open", pushed=it["commit_sha"], pr_seen=_now_iso(),
                      why=None)
    return f"item {it['id']}: PR {'found' if found else 'opened'} — {url}"


def _poll(conn: sqlite3.Connection, it: sqlite3.Row, project: sqlite3.Row) -> Optional[str]:
    got = github.pr_view(it["pr"])
    state = (got.get("state") or "").upper()
    if state == "MERGED":
        with db.tx(conn):
            selfwork._set(conn, it["id"], state="applied", pr_state="merged")
        if not workspace.remove(project["path"], it["id"], delete_branch=True):
            workspace.git(project["path"], "branch", "-D", f"eki/{it['id']}", check=False)
        return f"item {it['id']}: applied — merged on GitHub ({it['pr']})"
    if state == "CLOSED":
        comments = [c for c in got.get("comments") or [] if _first(c.get("body"))]
        why = "PR closed without merging"
        if comments:
            why += f": {_first(comments[-1]['body'])}"
        with db.tx(conn):
            selfwork._set(conn, it["id"], state="dropped", pr_state="closed", error=why)
        workspace.remove(project["path"], it["id"])
        return f"item {it['id']}: dropped — {why}"
    return None


def _close(conn: sqlite3.Connection, it: sqlite3.Row) -> str:
    github.pr_close(it["pr"], f"dropped in eki {github.MARK}")
    with db.tx(conn):
        selfwork._set(conn, it["id"], pr_state="closed")
    return f"item {it['id']}: dropped in eki; closed {it['pr']}"


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prs"):
        return []
    said: List[str] = []
    paths: Dict[str, Optional[str]] = {}               # project id -> repo, once per pass

    def repo_for(pid: str) -> tuple:
        project = projects.get(conn, pid)
        if project is None:
            return None, None
        if pid not in paths:
            paths[pid] = github.path_of(project["path"])[0]
        return project, paths[pid]

    work = ((_items(conn, "i.state='proposed' AND i.pr IS NULL"), "open"),
            (_items(conn, "i.state='proposed' AND i.pr_state='open'"), "poll"),
            (_items(conn, "i.state='dropped' AND i.pr_state='open'"), "close"))
    for rows, what in work:
        for it in rows:
            project, repo = repo_for(it["project_id"])
            if repo is None:
                continue
            try:
                if what == "open":
                    said.append(_open(conn, it, project, repo))
                elif what == "poll":
                    line = _poll(conn, it, project)
                    if line:
                        said.append(line)
                else:
                    said.append(_close(conn, it))
            except (github.GhError, workspace.WorkspaceError) as e:
                with db.tx(conn):
                    selfwork._set(conn, it["id"], why=f"GitHub: {_first(str(e))}")
                said.append(f"item {it['id']}: GitHub: {_first(str(e))}")
    return said
