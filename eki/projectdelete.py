"""A project leaves eki: recorded first, then finished — by the caller or the next housekeeping pass.

`begin` marks the row `deleting` in one transaction; from then on every
listing hides it. `finish` stops its work (runs cancelled, goals left,
items, standing goals and schedules dropped, open asks withdrawn), removes
eki's worktrees of its items and, for a GitHub row, eki's own clone under
~/.eki/projects, archives its chats (they keep `threads.project`, so they
are found under archived chats and rejoin the folder when it is added again),
and last turns the row `deleted` with its path freed. Every step reads its
work from the db and skips what is already done, so a restart anywhere just
runs it again (`tick`, one step in housekeep.STEPS).

The person's folder is never touched: nothing runs in it but `git worktree
remove` of eki's own worktrees, and no `eki/<id>` branch is deleted.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from . import db, paths, projectchats, projectsetup, standing, store, workspace

CONFIRM = ("Stops its work, archives its {chats} chats, removes eki's worktrees. "
           "Your folder {folder} is not touched.")
GOING = ("deleting", "deleted")
MARK = "#deleted-"
OVER = projectchats.OVER
PLANNING = ("drafting", "planning")


def _row(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    pid = projectchats.find(conn, key)
    if pid == projectchats.SELF:
        raise ValueError(f"eki can't be deleted: it's {projectchats.SELF_DESCRIPTION}")
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None or p["state"] in GOING:
        raise KeyError(f"no project {key}")
    return p


def _folder(p: sqlite3.Row) -> Optional[str]:
    """The person's folder: `folder`, else a local (or retired) row's path."""
    if p["folder"]:
        return projectchats.norm(p["folder"])
    if p["repo"] is None or p["state"] == "retired":
        return projectchats.norm(_path(p))
    return None


def _path(p: sqlite3.Row) -> str:
    """The row's path as it was before the delete freed it."""
    return p["path"].split(MARK, 1)[0]


def preview(conn: sqlite3.Connection, key: str) -> dict:
    """What a delete of `key` would do, in one line; same refusals as `delete`."""
    p = _row(conn, key)
    folder = _folder(p)
    chats = conn.execute("SELECT COUNT(*) FROM threads WHERE project=? AND deleted_at IS NULL",
                         (p["id"],)).fetchone()[0]
    return {"id": p["id"], "name": p["name"], "folder": folder, "chats": chats,
            "line": CONFIRM.format(chats=chats, folder=folder or "no folder")}


def delete(conn: sqlite3.Connection, key: str) -> dict:
    """Delete the project `key` (id, name or OWNER/REPO); returns what `finish` did."""
    p = _row(conn, key)
    begin(conn, p["id"])
    return finish(conn, p["id"])


def begin(conn: sqlite3.Connection, pid: str) -> None:
    """Record the delete: from now on the row is hidden, and `tick` finishes it if no one else does."""
    with db.tx(conn):
        conn.execute("UPDATE projects SET state='deleting' WHERE id=? AND COALESCE(state,'on')!='deleted'",
                     (pid,))


# ---- finish -----------------------------------------------------------------------------------

def finish(conn: sqlite3.Connection, pid: str) -> dict:
    """Stop, remove, archive, then mark deleted; safe to run again after a crash at any point."""
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise KeyError(f"no project {pid}")
    out = {"id": pid, "name": p["name"], "folder": _folder(p)}
    goals = _stop_goals(conn, pid)
    out["runs"] = _cancel(conn, _runs(conn, p))
    out["goals"] = goals
    out["items"] = _drop_items(conn, p)
    out["standing"] = _drop_standing(conn, pid)
    out["schedules"] = _drop_schedules(conn, pid)
    out["asks"] = _withdraw(conn, p)
    removed = _worktrees(conn, p)
    clone = _clone(p)
    if clone:
        removed.append(clone)
    out["chats"] = _archive(conn, pid)
    out["removed"] = removed
    ignored = _ignored(conn, p)
    with db.tx(conn):
        conn.execute(
            "UPDATE projects SET folder=COALESCE(folder, CASE WHEN repo IS NULL THEN path END), "
            "path=path || ? || id, state='deleted', ignored=COALESCE(?, ignored) "
            "WHERE id=? AND state!='deleted'", (MARK, ignored, pid))
    return out


def _goal_ids(conn: sqlite3.Connection, pid: str) -> List[str]:
    return [r[0] for r in conn.execute("SELECT id FROM goals WHERE project=?", (pid,))]


def _stop_goals(conn: sqlite3.Connection, pid: str) -> int:
    """Goals still planning, or planned with an item not over: left, 'project deleted'."""
    marks, plan = ",".join("?" * len(OVER)), ",".join("?" * len(PLANNING))
    with db.tx(conn):
        return conn.execute(
            f"UPDATE goals SET state='left', error='project deleted' WHERE project=? AND (state IN ({plan}) "
            f"OR (state='planned' AND EXISTS (SELECT 1 FROM items i WHERE i.goal_id=goals.id "
            f"AND i.state NOT IN ({marks}))))", (pid, *PLANNING, *OVER)).rowcount


def _runs(conn: sqlite3.Connection, p: sqlite3.Row) -> List[str]:
    """Every run of the project: its goals', its items', its setup run and its chats'."""
    pid = p["id"]
    rows = conn.execute(
        "SELECT draft_run FROM goals WHERE project=? UNION SELECT plan_run FROM goals WHERE project=? "
        "UNION SELECT i.run_id FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? "
        "UNION SELECT i.gate2_run FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? "
        "UNION SELECT i.review_run FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? "
        "UNION SELECT r.id FROM runs r JOIN threads t ON t.id=r.thread_id WHERE t.project=?",
        (pid,) * 6).fetchall()
    out = {r[0] for r in rows if r[0]}
    if p["setup_run"]:
        out.add(p["setup_run"])
    return sorted(out)


def _cancel(conn: sqlite3.Connection, rids: List[str]) -> int:
    n = 0
    for rid in rids:
        r = store.run(conn, rid)
        if r is None or r["state"] not in store.ACTIVE or r["cancel"]:
            continue                                     # ended, or told to stop already
        store.cancel(conn, rid)
        n += 1
    return n


def _drop_items(conn: sqlite3.Connection, p: sqlite3.Row) -> int:
    """Items not over: dropped (their runs are cancelled above). Worktrees go in `_worktrees`."""
    marks = ",".join("?" * len(OVER))
    with db.tx(conn):
        return conn.execute(
            f"UPDATE items SET state='dropped' WHERE state NOT IN ({marks}) AND goal_id IN "
            f"(SELECT id FROM goals WHERE project=?)", (*OVER, p["id"])).rowcount


def _drop_standing(conn: sqlite3.Connection, pid: str) -> int:
    rows = conn.execute("SELECT id FROM standing WHERE project=? AND state!='dropped'", (pid,)).fetchall()
    for r in rows:
        standing.drop(conn, r["id"])
    return len(rows)


def _drop_schedules(conn: sqlite3.Connection, pid: str) -> int:
    with db.tx(conn):
        return conn.execute("UPDATE schedules SET state='dropped', why='project deleted' "
                            "WHERE project=? AND state!='dropped'", (pid,)).rowcount


def _withdraw(conn: sqlite3.Connection, p: sqlite3.Row) -> int:
    rids = _runs(conn, p)
    marks = ",".join("?" * len(rids)) or "NULL"
    with db.tx(conn):
        return conn.execute(
            f"UPDATE asks SET state='withdrawn' WHERE state='open' AND (run_id IN ({marks}) OR thread_id IN "
            f"(SELECT id FROM threads WHERE project=?))", (*rids, p["id"])).rowcount


def _worktrees(conn: sqlite3.Connection, p: sqlite3.Row) -> List[str]:
    """eki's worktrees of the project's items under ~/.eki/work; every branch is kept."""
    repo = Path(_path(p))
    removed: List[str] = []
    for gid in _goal_ids(conn, p["id"]):
        for it in conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchall():
            where = workspace.path_for(it["id"])
            if not where.exists():
                continue
            if (repo / ".git").exists():
                workspace.remove(repo, it["id"], delete_branch=False)
            else:                                        # the repo is gone: only eki's folder is left
                shutil.rmtree(where, ignore_errors=True)
            if not where.exists():
                removed.append(str(where))
    return removed


def _clone(p: sqlite3.Row) -> Optional[str]:
    """eki's own clone of a GitHub row (and its `.part`), only when under ~/.eki/projects."""
    if not p["repo"]:
        return None
    clone = Path(_path(p))
    home = paths.projects().resolve()
    if home not in clone.resolve().parents:
        return None
    part = Path(f"{clone}.part")
    if not clone.exists() and not part.exists():
        return None
    shutil.rmtree(clone, ignore_errors=True)
    shutil.rmtree(part, ignore_errors=True)
    return str(clone)


def _archive(conn: sqlite3.Connection, pid: str) -> int:
    with db.tx(conn):
        return conn.execute("UPDATE threads SET archived_at=? WHERE project=? AND archived_at IS NULL",
                            (db.now(), pid)).rowcount


def _ignored(conn: sqlite3.Connection, p: sqlite3.Row) -> Optional[str]:
    """A GitHub row's issues open now: not taken again until a newer one (as a drop does, eki/issues.py)."""
    if not p["repo"] or p["state"] == "retired":
        return None
    got, _ = projectsetup.open_numbers(conn, p["repo"], "?", [p["id"]])
    try:
        got = sorted(set(got) | {int(n) for n in json.loads(p["ignored"] or "[]")})
    except (ValueError, TypeError):
        pass
    return json.dumps(got)


# ---- the tick ---------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    """Finish every delete a restart interrupted; one line each."""
    said: List[str] = []
    for p in conn.execute("SELECT id FROM projects WHERE state='deleting' ORDER BY created_at").fetchall():
        said.append(line(finish(conn, p["id"])))
    return said


def line(done: Dict) -> str:
    """What a delete did, in one line."""
    return (f"{done['name']}: deleted — {done['runs']} run(s) cancelled, {done['goals']} goal(s) stopped, "
            f"{done['items']} item(s) dropped, {done['standing']} standing goal(s) dropped, "
            f"{done['asks']} ask(s) closed, {done['chats']} chat(s) archived, "
            f"{len(done['removed'])} folder(s) removed; {done['folder'] or 'no folder'} not touched")
