"""Projects you work inside: a name and your folder, with chats in it.

A project is a `projects` row, plus eki itself: the built-in entry `SELF`
(name `eki`, folder `builds.source()`, background work the self goals,
`goals.project IS NULL`). It has no row, so nothing that loops over
`projects` ever sees it.

Two folders, two meanings. `projects.folder` is your real folder, where a
project's chats run (`threads.project`). `projects.path` keeps its meaning:
where background work is based — the same folder for a row with `repo` NULL,
eki's own clone for a GitHub row. A GitHub row has no folder of its own
until `home_clone` finds one in your home, or you set one (`set_folder`).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from . import builds, db, github, integration, paths, store, workspace

SELF = "eki"
HIDDEN = ("dropped", "merged", "deleting", "deleted")
INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")
OVER = ("applied", "dropped", "left", "unfit", "landed", "live", "rolled back")
#: eki's description, fixed: it can't be edited
SELF_DESCRIPTION = "eki itself — its own source"


def norm(path: str | Path) -> str:
    """A folder as compared everywhere here: realpath, no trailing slash."""
    return os.path.realpath(os.path.expanduser(str(path))).rstrip("/") or "/"


def _under(path: str, root: Path) -> bool:
    r = norm(root)
    return path == r or path.startswith(r + "/")


def _row_folder(p: sqlite3.Row) -> Optional[str]:
    """folder; else path for a row on the local path (a retired row's path is your folder too)."""
    if p["folder"]:
        return norm(p["folder"])
    if p["repo"] is None or p["state"] == "retired":
        return norm(p["path"])
    return None


def folder_of(conn: sqlite3.Connection, pid: str) -> Optional[str]:
    """Where the project's chats run, or None (a GitHub row with no folder yet)."""
    if pid == SELF:
        return norm(builds.source())
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise KeyError(f"no project {pid}")
    return _row_folder(p)


def _live(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Rows not dropped, merged or deleted, oldest first, without retired rows a live row's folder shadows,
    and never a row on eki's own repo, whatever its state: that is the built-in eki."""
    marks = ",".join("?" * len(HIDDEN))
    own = integration.own_repo()
    rows = [p for p in conn.execute(f"SELECT * FROM projects WHERE COALESCE(state,'on') NOT IN ({marks}) "
                                    "ORDER BY created_at", HIDDEN).fetchall()
            if own is None or p["repo"] != own]
    live = {_row_folder(p) for p in rows if p["state"] != "retired"} - {None}
    return [p for p in rows if not (p["state"] == "retired" and _row_folder(p) in live)]


def _shadowed(conn: sqlite3.Connection) -> Dict[str, str]:
    """Retired row id -> the live row id whose folder it shares."""
    marks = ",".join("?" * len(HIDDEN))
    rows = conn.execute(f"SELECT * FROM projects WHERE COALESCE(state,'on') NOT IN ({marks}) "
                        "ORDER BY created_at", HIDDEN).fetchall()
    live: Dict[str, str] = {}
    for p in rows:
        f = _row_folder(p)
        if p["state"] != "retired" and f is not None:
            live.setdefault(f, p["id"])
    return {p["id"]: live[_row_folder(p)] for p in rows
            if p["state"] == "retired" and _row_folder(p) in live}


def _folders(conn: sqlite3.Connection) -> Dict[str, str]:
    """folder -> project id, eki first, for every listed project with a folder."""
    out = {norm(builds.source()): SELF}
    for p in _live(conn):
        f = _row_folder(p)
        if f is not None:
            out.setdefault(f, p["id"])
    return out


# ---- adopt ------------------------------------------------------------------------------------

def adopt(conn: sqlite3.Connection) -> int:
    """Put chats with no project into the project whose folder they run in; returns how many moved.

    A thread that has a project keeps it — except one on a retired row a live row shadows, or on a
    deleted row whose folder a live row has, whose chats belong to the live row. Engine threads (under EKI_HOME) never match."""
    folders = _folders(conn)
    moves: List[tuple] = []
    for r in conn.execute("SELECT DISTINCT cwd FROM threads WHERE project IS NULL AND cwd IS NOT NULL "
                          "AND cwd!=''").fetchall():
        where = norm(r["cwd"])
        if _under(where, paths.home()):
            continue
        pid = folders.get(where)
        if pid is not None:
            moves.append(("UPDATE threads SET project=? WHERE project IS NULL AND cwd=?", (pid, r["cwd"])))
    for old, new in _shadowed(conn).items():
        moves.append(("UPDATE threads SET project=? WHERE project=?", (new, old)))
    for p in conn.execute("SELECT * FROM projects WHERE state='deleted' AND folder IS NOT NULL").fetchall():
        pid = folders.get(norm(p["folder"]))
        if pid is not None and pid != SELF:              # a deleted row's chats join its folder, still archived
            moves.append(("UPDATE threads SET project=? WHERE project=?", (pid, p["id"])))
    if not moves:
        return 0
    n = 0
    with db.tx(conn):
        for sql, args in moves:
            n += conn.execute(sql, args).rowcount
    return n


# ---- listing ----------------------------------------------------------------------------------

def home_clone(repo: str) -> Optional[str]:
    """The first folder directly in your home whose configured origin is `repo`, or None."""
    root = paths.user_home()
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / ".git").exists() or _under(norm(d), paths.home()):
            continue
        if github.repo_of(d) == repo:
            return norm(d)
    return None


def _scan(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM projects WHERE repo IS NOT NULL AND folder IS NULL "
                        "AND COALESCE(state,'on') NOT IN ('dropped','merged','retired','deleting','deleted') "
                        "ORDER BY created_at").fetchall()
    for p in rows:
        hit = home_clone(p["repo"])
        if hit is None or hit == norm(builds.source()):
            continue
        with db.tx(conn):
            conn.execute("UPDATE projects SET folder=? WHERE id=?", (hit, p["id"]))


def _counts(conn: sqlite3.Connection, pid: str) -> Dict[str, int]:
    own = "g.project IS NULL" if pid == SELF else "g.project=?"
    args = () if pid == SELF else (pid,)
    marks = ",".join("?" * len(OVER))
    goals = conn.execute(
        f"SELECT COUNT(*) FROM goals g WHERE {own} AND (g.state IN ('drafting','planning') OR "
        f"(g.state='planned' AND EXISTS (SELECT 1 FROM items i WHERE i.goal_id=g.id "
        f"AND i.state NOT IN ({marks}))))", (*args, *OVER)).fetchone()[0]
    stand = conn.execute(f"SELECT COUNT(*) FROM standing g WHERE {own} AND g.state='on'", args).fetchone()[0]
    prs = conn.execute(f"SELECT COUNT(*) FROM items i JOIN goals g ON g.id=i.goal_id "
                       f"WHERE {own} AND i.pr_state='open'", args).fetchone()[0]
    chats = conn.execute("SELECT COUNT(*) FROM threads WHERE project=?", (pid,)).fetchone()[0]
    return {"chats": chats, "goals": goals + stand, "prs": prs}


def _has_work(conn: sqlite3.Connection, pid: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM goals WHERE project=? UNION ALL "
                             "SELECT 1 FROM standing WHERE project=? AND state!='dropped' LIMIT 1",
                             (pid, pid)).fetchone())


def _last_active(conn: sqlite3.Connection, pid: str, created: Optional[float]) -> Optional[float]:
    """The latest of the project's `created_at` and the newest run in its chats."""
    run = conn.execute("SELECT MAX(r.created_at) FROM runs r JOIN threads t ON t.id=r.thread_id "
                       "WHERE t.project=?", (pid,)).fetchone()[0]
    return max((t for t in (created, run) if t is not None), default=None)


def _entry(conn: sqlite3.Connection, pid: str, name: str, folder: Optional[str], repo: Optional[str],
           kind: str, background: Optional[str], description: Optional[str] = None,
           created: Optional[float] = None) -> dict:
    ok = bool(folder) and os.path.isdir(folder)
    if repo is None and ok and workspace.repo_of(folder) is not None:
        repo = github.repo_of(folder)
    found = next((f for f in INSTRUCTIONS if ok and os.path.isfile(os.path.join(folder, f))), None)
    return {"id": pid, "name": name, "folder": folder, "folder_ok": ok, "repo": repo, "kind": kind,
            **_counts(conn, pid), "instructions": found, "background": background,
            "description": description, "last_active": _last_active(conn, pid, created)}


def entry(conn: sqlite3.Connection, p: sqlite3.Row) -> dict:
    """One project row as listed."""
    folder = _row_folder(p)
    more = {"description": p["description"], "created": p["created_at"]}
    if p["repo"] and p["state"] != "retired":
        own = p["lands"] == "github" and folder is not None and norm(p["path"]) == folder
        how = (f"worktrees of {folder}; changes come as PRs on {p['repo']}" if own
               else f"eki's clone at {p['path']}; changes come as PRs")
        return _entry(conn, p["id"], p["name"], folder, p["repo"], "github", how, **more)
    background = (f"worktrees of {folder}; eki/<id> branches you merge"
                  if _has_work(conn, p["id"]) else None)
    return _entry(conn, p["id"], p["name"], folder, p["repo"], "folder", background, **more)


def listing(conn: sqlite3.Connection, scan: bool = False) -> List[dict]:
    """eki first, then every project not dropped, merged or deleted. `scan` looks in your home for the
    folders of GitHub rows that have none (never from the sidebar poll)."""
    if scan:
        _scan(conn)
    adopt(conn)
    out = [_entry(conn, SELF, "eki", norm(builds.source()), None, "eki", "eki's own queue",
                  SELF_DESCRIPTION)]
    return out + [entry(conn, p) for p in _live(conn)]


# ---- making and changing ----------------------------------------------------------------------

def _refuse(where: str) -> None:
    if _under(where, paths.home()):
        raise ValueError(f"{where} is inside eki's home ({paths.home()}): pick a folder of your own")
    if where == norm(builds.source()):
        raise ValueError(f"{where}: that's eki itself")


def _same_folder(conn: sqlite3.Connection, where: str) -> Optional[str]:
    for p in _live(conn):
        if _row_folder(p) == where:
            return p["id"]
    got = conn.execute("SELECT id, state FROM projects WHERE path=?", (where,)).fetchone()
    if got is not None and got["state"] == "deleting":
        raise ValueError(f"{where} is being deleted from eki: add it again in a moment")
    return got["id"] if got else None


def _clean(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def create(conn: sqlite3.Connection, name: str, folder: Optional[str | Path] = None,
           description: Optional[str] = None) -> str:
    """A project named `name` on `folder` (default ~/<name>, made and `git init`ed when missing),
    and what you're trying to achieve there. Nothing is written in the folder but `git init`."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a project needs a name")
    if name.lower() == SELF:
        raise ValueError("eki is already a project: it's eki itself")
    if folder is None and ("/" in name or name.startswith(".")):
        raise ValueError(f"{name!r} can't be a folder name: give --folder")
    target = Path(folder).expanduser() if folder is not None else paths.user_home() / name
    where = norm(target)
    _refuse(where)
    got = _same_folder(conn, where)
    if got is not None:
        return got
    if not os.path.exists(where):
        os.makedirs(where)
        workspace.git(where, "init", "-q")
    elif not os.path.isdir(where):
        raise ValueError(f"{where} is not a folder")
    branch = ""
    if workspace.repo_of(where) is not None and workspace.git(
            where, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        branch = workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, folder, branch, created_at, description) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, name, where, where, branch, db.now(), _clean(description)))
    adopt(conn)
    return pid


def set_folder(conn: sqlite3.Connection, pid: str, folder: str | Path) -> str:
    """Where the project's chats run from now on; returns the folder as recorded."""
    if pid == SELF:
        raise ValueError("eki's folder is its own source; it can't be changed")
    if conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() is None:
        raise KeyError(f"no project {pid}")
    where = norm(folder)
    if not os.path.isdir(where):
        raise ValueError(f"{where} is not a folder")
    _refuse(where)
    with db.tx(conn):
        conn.execute("UPDATE projects SET folder=? WHERE id=?", (where, pid))
    adopt(conn)
    return where


def set_description(conn: sqlite3.Connection, pid: str, text: Optional[str]) -> Optional[str]:
    """What the project is for, from now on (empty clears it); returns it as recorded."""
    pid = find(conn, pid)
    if pid == SELF:
        raise ValueError("eki's description is fixed: it's eki itself")
    text = _clean(text)
    with db.tx(conn):
        conn.execute("UPDATE projects SET description=? WHERE id=?", (text, pid))
    return text


def find(conn: sqlite3.Connection, key: str) -> str:
    """A project id from an id, `eki`, a name or OWNER/REPO. KeyError when unknown; ValueError
    when a name is two projects."""
    key = (key or "").strip()
    if key == SELF:
        return SELF
    got = conn.execute("SELECT state FROM projects WHERE id=?", (key,)).fetchone()
    if got is not None and got["state"] in ("deleting", "deleted"):
        raise KeyError(f"no project {key}: it was deleted")
    if got is not None:
        return key
    live = _live(conn)
    named = [p for p in live if p["name"] == key]
    if len(named) > 1:
        raise ValueError(f"{key} is {len(named)} projects: "
                         + ", ".join(f"{p['id']} ({_row_folder(p) or p['path']})" for p in named)
                         + " — say which by id")
    if named:
        return named[0]["id"]
    if "/" in key:
        if key == integration.own_repo():
            return SELF
        hits = [p for p in live if p["repo"] == key and p["state"] != "retired"]
        hits = hits or [p for p in live if p["repo"] == key]
        if hits:
            return hits[0]["id"]
        for e in listing(conn):
            if e["repo"] == key and e["id"] != SELF:
                return e["id"]
    raise KeyError(f"no project {key}")
