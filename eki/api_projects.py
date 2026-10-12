"""The window's project pages: the list, one project with its sections, and
what the page can do — make a project, pick its folder, clone it to your
home, have an agent write its instructions. `server.py` does the HTTP.

Nothing here keeps state of its own. A clone is a `command` run on a thread
titled `clone <repo> to <dest>`, and `projects.folder` is set to `dest` in
the same transaction, so a restart loses nothing: the page says `cloning…`
while that run is active and `dest/.git` is still missing.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import (asking, db, gallery, github, paths, projectchats, projectdelete, projectgithub, projectlands,
               projectstart, store)

#: what the page's "Have an agent write instructions" asks for
INSTRUCTIONS_PROMPT = (
    "Look at this folder: what it is, how it is laid out, how to build, run and test it, "
    "and the conventions its code follows. Write AGENTS.md here with what an agent needs "
    "to work in this folder — short and concrete. Then write CLAUDE.md containing only "
    "the line `@AGENTS.md`, so Claude Code and Codex both read the one copy.")

#: how many event rows are read looking for a project's files still on disk
_FILE_EVENTS = 2000
_ACTIVE = ("queued", "starting", "running")

#: put before INSTRUCTIONS_PROMPT when the project has a description
DESCRIBED_PROMPT = (
    "Make the first section of AGENTS.md a heading `# {name}` followed by this description, "
    "verbatim:\n\n{description}\n\nThen, after it:\n\n")

#: how many folders `folders` offers
_FOLDERS = 20


def projects_list(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Every project (eki first) and your home, to prefill a new project's `~/<name>`."""
    return {"projects": projectchats.listing(conn, scan=True), "user_home": str(paths.user_home())}


def _entry(conn: sqlite3.Connection, pid: str) -> dict:
    for e in projectchats.listing(conn, scan=True):
        if e["id"] == pid:
            return e
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError(f"no project {pid}")
    return projectchats.entry(conn, row)        # dropped or merged: still shown when asked for


def show(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """One project as listed (the counts), a header line, and its sections: chats, background
    (goals, standing goals, open PRs), pictures, files."""
    e = _entry(conn, pid)
    pics = gallery.pictures(conn, 60, project=pid)
    folder = e["folder"] or "no folder yet"
    return {**e,
            "header": f"Chats work in {folder} — your real folder. "
                      f"Background: {e['background'] or 'none'}",
            "sections": {"chats": _chats(conn, pid), "background": _background(conn, pid),
                         "pictures": {"count": len(pics), "items": pics},
                         "files": _files(conn, pid)},
            "cloning": _cloning(conn, pid)}


def _chats(conn: sqlite3.Connection, pid: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.*, MAX(r.created_at) AS updated, "
        " SUM(CASE WHEN r.state IN ('queued','starting','running') THEN 1 ELSE 0 END) AS active "
        "FROM threads t LEFT JOIN runs r ON r.thread_id = t.id WHERE t.project = ? "
        "GROUP BY t.id ORDER BY COALESCE(updated, t.created_at) DESC", (pid,)).fetchall()
    return [{"id": t["id"], "title": t["title"], "updated": t["updated"] or t["created_at"],
             "working": bool(t["active"])} for t in rows]


def _background(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """The project's goals with their state, its standing goals, and its open PRs."""
    own = "project IS NULL" if pid == projectchats.SELF else "project = ?"
    args = () if pid == projectchats.SELF else (pid,)
    goals = conn.execute(f"SELECT id, text, state, created_at FROM goals WHERE {own} "
                         "ORDER BY created_at DESC LIMIT 30", args).fetchall()
    standing = conn.execute(f"SELECT id, text, state FROM standing WHERE {own} AND state != 'dropped' "
                            "ORDER BY rowid", args).fetchall()
    prs = conn.execute(f"SELECT i.id, i.title, i.pr FROM items i JOIN goals g ON g.id = i.goal_id "
                       f"WHERE g.{own} AND i.pr_state = 'open' ORDER BY i.updated_at DESC",
                       args).fetchall()
    return {"goals": [dict(g) for g in goals], "standing": [dict(s) for s in standing],
            "prs": [{"item": p["id"], "title": p["title"], "url": p["pr"]} for p in prs]}


def _files(conn: sqlite3.Connection, pid: str) -> List[str]:
    """Paths the project's chat runs wrote (the events `api._run_view` lists as files), still
    on disk, newest first, once each."""
    rows = conn.execute(
        "SELECT json_extract(e.data, '$.path') AS path FROM events e "
        "JOIN runs r ON r.id = e.run_id JOIN threads t ON t.id = r.thread_id "
        "WHERE t.project = ? AND e.kind IN ('tool','note','interrupted','handoff') "
        "AND json_extract(e.data, '$.path') IS NOT NULL ORDER BY e.t DESC, e.id DESC LIMIT ?",
        (pid, _FILE_EVENTS)).fetchall()
    out: List[str] = []
    for r in rows:
        path = r["path"]
        if isinstance(path, str) and path and path not in out and os.path.isfile(path):
            out.append(path)
    return out


def _clone_title(repo: str, dest: str) -> str:
    return projectgithub.clone_title(repo, dest)


def _cloning(conn: sqlite3.Connection, pid: str) -> bool:
    """A `clone <repo> to <folder>` run for the project's folder is active (any repo: a row that
    lands here has none)."""
    row = conn.execute("SELECT folder FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None or not row["folder"]:
        return False
    if os.path.isdir(os.path.join(row["folder"], ".git")):
        return False
    marks = ",".join("?" * len(_ACTIVE))
    tail = " to " + row["folder"]
    return bool(conn.execute(
        f"SELECT 1 FROM runs r JOIN threads t ON t.id = r.thread_id WHERE t.title LIKE 'clone %' "
        f"AND substr(t.title, -?) = ? AND r.state IN ({marks}) LIMIT 1",
        (len(tail), tail, *_ACTIVE)).fetchone())


# ---- what the page can do -------------------------------------------------------------------

def _expand(folder: Optional[str]) -> Optional[str]:
    """A folder typed on the page; `~` is your home as eki knows it (`paths.user_home()`)."""
    folder = (folder or "").strip()
    if not folder:
        return None
    if folder == "~" or folder.startswith("~/"):
        return str(paths.user_home() / folder[2:])
    return folder


def create(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/projects {name, description?, folder?, github?, lands?, create_repo?, private?}:
    {id, folder, lands, cloning, existing, warning?}. With `github`, the repository's clone
    (projectgithub.start). Start new (no folder) with `create_repo` makes a GitHub repository for it
    too; gh failing leaves the project made, landing here, and says why in `warning`."""
    if str(body.get("github") or "").strip():
        return projectgithub.start(conn, body)
    lands = body.get("lands") or None
    if lands is not None and lands not in projectlands.LANDS:
        raise ValueError(f"work lands {' or '.join(projectlands.LANDS)}, not {lands!r}")
    typed = str(body.get("folder") or "").strip()
    if lands == "github" and not body.get("create_repo"):
        _can_github(typed)
    made = projectstart.create(conn, body)
    pid, warning = made["id"], None
    if made.get("ask") or pid == projectchats.SELF:
        return made
    if made.get("existing"):
        pass                                        # a project already there keeps what it said
    elif body.get("create_repo") and not typed:
        folder = made["folder"]
        try:
            projectlands.mark(conn, pid, "github",
                              github.repo_create(os.path.basename(folder), folder, body.get("private", True) is not False))
        except (github.GhError, ValueError) as e:
            projectlands.mark(conn, pid, "here")
            warning = f"the project is made, but not its GitHub repository: {e}"
    elif lands is not None:
        projectlands.set_lands(conn, pid, lands)
    out = {**made, "lands": projectlands.lands_of(conn, pid)["lands"], "cloning": _cloning(conn, pid)}
    if warning:
        out["warning"] = warning
    return out


def _can_github(typed: str) -> None:
    """Pull requests need a folder whose origin is on GitHub, and gh signed in."""
    where = projectstart.expand(typed) if typed else None
    if where is None or not os.path.isdir(where) or github.repo_of(where) is None:
        raise ValueError("pull requests need a folder with a GitHub remote: "
                         "pick 'eki merges here', or start new with a GitHub repository")
    ok, why = projectlands._signed_in()
    if not ok:
        raise ValueError(f"pull requests need gh — {why}")


def clone_to(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """GET /api/github/clone-to?repo=: where the New project sheet's From GitHub would clone it."""
    return projectgithub.clone_to(q.get("repo") or "")


def start(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """GET /api/projectstart?name= or ?folder=: where it would go, recent folders, what's there."""
    return projectstart.proposal(conn, q.get("name"), q.get("folder"))


def starter(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """GET /api/projects/<id>/starter: the cards to suggest for what is in its folder."""
    return projectstart.starter(conn, m.group(1))


def describe(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/projects/<id>/describe {description}: the project as listed after."""
    pid = projectchats.find(conn, m.group(1))
    projectchats.set_description(conn, pid, str(body.get("description") or ""))
    return _entry(conn, pid)


def folders(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """GET /api/folders?prefix=: up to 20 folders under what's typed, written the way it was typed
    (`~` is `paths.user_home()`; a relative path is under it). Dot-folders only when the last
    part starts with `.`; nothing outside your home."""
    typed = q.get("prefix") or ""
    if typed == "~":
        typed = "~/"
    head, part = typed.rsplit("/", 1) if "/" in typed else ("", typed)
    head = head + "/" if "/" in typed else ""
    home = os.path.realpath(paths.user_home())
    base = os.path.realpath(os.path.join(home, _expand(head) or "."))
    if not (base == home or base.startswith(home.rstrip("/") + "/")) or not os.path.isdir(base):
        return {"folders": []}
    try:
        names = sorted(os.listdir(base), key=str.lower)
    except OSError:
        return {"folders": []}
    out = [head + n for n in names
           if n.lower().startswith(part.lower()) and (part.startswith(".") or not n.startswith("."))
           and os.path.isdir(os.path.join(base, n))]
    return {"folders": out[:_FOLDERS]}


def set_folder(conn: sqlite3.Connection, pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    folder = _expand(body.get("folder"))
    if not folder:
        raise ValueError("which folder?")
    return {"id": pid, "folder": projectchats.set_folder(conn, pid, folder)}


def clone_home(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """Clone a GitHub project to ~/<repo name> as a run; its folder is that from now on."""
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError(f"no project {pid}")
    if not row["repo"]:
        raise ValueError(f"{row['name']} is not on GitHub: there is nothing to clone")
    home = paths.user_home()
    dest = Path(os.path.realpath(home)) / row["repo"].split("/")[-1]
    if os.path.lexists(dest):
        raise ValueError(f"{dest} already exists: pick it as the folder instead")
    with db.tx(conn):
        tid = store.create_thread(conn, "", str(home), by="eki")
        conn.execute("UPDATE threads SET title=? WHERE id=?",       # whole: `_cloning` finds it by title
                     (_clone_title(row["repo"], str(dest)), tid))
        rid = store.create_run(conn, tid, json.dumps(["gh", "repo", "clone", row["repo"], str(dest)]),
                               provider="command")
        conn.execute("UPDATE projects SET folder=? WHERE id=?", (str(dest), pid))
    return {"thread": tid, "run": rid, "folder": str(dest)}


def instructions_prompt(conn: sqlite3.Connection, pid: str) -> str:
    """INSTRUCTIONS_PROMPT, led by the description as AGENTS.md's first section when there is one.
    The prompt is the only place eki puts it; the agent writes the file."""
    row = conn.execute("SELECT name, description FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None or not row["description"]:
        return INSTRUCTIONS_PROMPT
    return DESCRIBED_PROMPT.format(name=row["name"], description=row["description"]) + INSTRUCTIONS_PROMPT


def write_instructions(conn: sqlite3.Connection, pid: str) -> Dict[str, Any]:
    """A chat in the project that writes AGENTS.md, and CLAUDE.md pointing at it."""
    tid, rid = asking.submit(conn, instructions_prompt(conn, pid), project=pid)
    return {"thread": tid, "run": rid}


def remove(conn: sqlite3.Connection, m: Any, q: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    """DELETE /api/projects/<id>: the project leaves eki (your folder stays); what was stopped and removed."""
    return projectdelete.delete(conn, m.group(1))


#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("POST", r"/api/projects/([^/]+)/describe", describe),
    ("GET", r"/api/folders", folders),
    ("GET", r"/api/github/clone-to", clone_to),
    ("GET", r"/api/projectstart", start),
    ("GET", r"/api/projects/([\w.-]+)/starter", starter),
    ("DELETE", r"/api/projects/([^/]+)", remove),
]
