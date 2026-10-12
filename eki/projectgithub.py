"""A new project from a GitHub repository, for the window's New project sheet.

`clone_to` says where the clone goes before anything is made: ~/Projects/<repo name>, or the
clone of it already there. `start` makes the project on that folder. A clone is a `command` run
`gh repo clone <repo> <dest>` on a thread titled `clone <repo> to <dest>`, inserted with the row
in one transaction and never waited on, so a restart loses nothing: the page says cloning while
that run is active (api_projects._cloning). Nothing here mkdirs or `git init`s the folder; gh
makes it. Where the work lands is said once, through projectlands.mark.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict

from . import db, github, paths, projectchats, projectlands, projects, projectstart, store

BAD_NAME = "say OWNER/REPO or a GitHub URL"


def clone_title(repo: str, dest: str) -> str:
    return f"clone {repo} to {dest}"


def clone_to(repo: str) -> Dict[str, Any]:
    """Where `repo` (OWNER/REPO or a URL) is cloned: {repo, folder, exists, reuse, error}."""
    name = github.named(repo)
    if name is None:
        return {"repo": None, "folder": None, "exists": False, "reuse": False, "error": BAD_NAME}
    folder = projectchats.norm(paths.user_home() / projectstart.DEFAULT_BASE / name.split("/")[-1])
    exists = os.path.lexists(folder)
    reuse = exists and os.path.isdir(folder) and github.repo_of(folder) == name
    error = ""
    if exists and not reuse:
        error = f"{folder} is already there and isn't a clone of {name}: move it, or open it as a folder"
    return {"repo": name, "folder": folder, "exists": exists, "reuse": reuse, "error": error}


def _answer(conn: sqlite3.Connection, pid: str, existing: bool = False) -> Dict[str, Any]:
    from .api_projects import _cloning
    row = projects.get(conn, pid)
    return {"id": pid, "folder": projectchats.folder_of(conn, pid),
            "lands": "github" if row["repo"] or row["lands"] == "github" else "here",
            "cloning": _cloning(conn, pid), "existing": existing}


def start(conn: sqlite3.Connection, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/projects {github, name?, description?, lands?}: the project on the repository's clone."""
    repo = github.named(str(body.get("github") or ""))
    if repo is None:
        raise ValueError(BAD_NAME)
    lands = body.get("lands") or "github"
    if lands not in projectlands.LANDS:
        raise ValueError(f"work lands {' or '.join(projectlands.LANDS)}, not {lands!r}")
    where = clone_to(repo)
    if where["error"]:
        raise ValueError(where["error"])
    dest = where["folder"]
    projectchats._refuse(dest)
    got = projectchats._same_folder(conn, dest)
    if got is not None:
        return _answer(conn, got, existing=True)
    other = projects.by_repo(conn, repo)
    if other is not None and lands == "github":
        raise ValueError(f"{repo} is already the project {other['name']} ({other['id']})")
    name = str(body.get("name") or "").strip() or repo.split("/")[-1]
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, folder, branch, created_at, description) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, name, dest, dest, "", db.now(),
                                                (str(body.get("description") or "")).strip() or None))
        if not where["reuse"]:
            home = str(paths.user_home())
            tid = store.create_thread(conn, "", home, by="eki")
            conn.execute("UPDATE threads SET title=? WHERE id=?", (clone_title(repo, dest), tid))
            store.create_run(conn, tid, json.dumps(["gh", "repo", "clone", repo, dest]), provider="command")
    projectlands.mark(conn, pid, lands, repo)
    return _answer(conn, pid)
