"""eki's own landed work, mirrored read-only as PRs against eki/landed.

Only when self.pr_mirror is on and the integration repo's origin is on
GitHub (github.path_of): otherwise no gh process at all. Each pass

  - pushes the running build's commit as `eki/landed` (fast-forward only;
    a rejected push is a log line, never forced);
  - opens a PR `eki/<id>` -> `eki/landed` for each of eki's own items that
    landed in the last 24 h, pushing its landed commit (rebased, else
    commit_sha) — one found by `pr list` is reused;
  - closes the PR once the item is live ("already live in build …") or
    rolled back, unless GitHub already shows it merged.

The PRs are for reading: the integration repo's main, the queue, the train,
the swap and integration.push never look at them. items.pr_state says where
each stands; projects.open_prs counts project items only, never these.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import List

from . import db, github, integration, selfwork, train

log = logging.getLogger("eki.prmirror")

BASE = "eki/landed"
WINDOW = 24 * 3600

_OWN = "(SELECT project FROM goals WHERE goals.id = items.goal_id) IS NULL"


def tick(conn: sqlite3.Connection) -> List[str]:
    if not github.due("prmirror"):
        return []
    if not selfwork.settings().get("pr_mirror"):
        return []
    where = integration.repo()
    repo, _why = github.path_of(where)
    if repo is None:
        return []
    said: List[str] = []
    sha = train.running_commit()
    if sha:
        try:
            github.push(where, sha, BASE)
        except github.GhError as e:
            said.append(_fail(f"couldn't move {BASE} to {sha[:7]}: {e}"))
    since = time.time() - WINDOW
    for it in conn.execute(f"SELECT * FROM items WHERE state IN ('landed','live') AND pr IS NULL "
                           f"AND landed_at >= ? AND {_OWN}", (since,)).fetchall():
        said.extend(_open(conn, where, repo, it))
    for it in conn.execute(f"SELECT * FROM items WHERE pr_state='open' AND pr IS NOT NULL "
                           f"AND state IN ('live','rolled back') AND {_OWN}").fetchall():
        said.extend(_close(conn, it))
    return said


def _fail(text: str) -> str:
    log.warning("prmirror: %s", text)
    return f"prmirror: {text}"


def _open(conn: sqlite3.Connection, where, repo: str, it: sqlite3.Row) -> List[str]:
    sha = it["rebased"] or it["commit_sha"]
    if not sha:
        return []
    branch = f"eki/{it['id']}"
    try:
        github.push(where, sha, branch)
        found = github.pr_find(repo, branch)
        url = found["url"] if found else github.pr_create(
            repo, base=BASE, branch=branch, title=f"[landed] {it['title']}",
            body=f"Landed in integration main at {sha[:7]}; read-only — goes live with the next train. "
                 f"Don't merge. {github.MARK}")
    except github.GhError as e:
        return [_fail(f"item {it['id']}: couldn't open its PR: {e}")]
    with db.tx(conn):
        conn.execute("UPDATE items SET pr=?, pr_state='open', pushed=? WHERE id=? AND pr IS NULL",
                     (url, sha, it["id"]))
    return [f"item {it['id']}: mirrored as {url}"]


def _close(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    url = it["pr"]
    try:
        if it["state"] == "live":
            if (github.pr_view(url).get("state") or "").upper() == "MERGED":
                _state(conn, it["id"], "merged")
                return [f"item {it['id']}: its PR was merged on GitHub"]
            github.pr_close(url, f"already live in build {it['build']} {github.MARK}")
        else:
            github.pr_close(url, f"rolled back: {it['error'] or 'no reason given'} {github.MARK}")
    except github.GhError as e:
        return [_fail(f"item {it['id']}: couldn't close {url}: {e}")]
    _state(conn, it["id"], "closed")
    return [f"item {it['id']}: closed {url} ({it['state']})"]


def _state(conn: sqlite3.Connection, iid: str, state: str) -> None:
    with db.tx(conn):
        conn.execute("UPDATE items SET pr_state=? WHERE id=?", (state, iid))
