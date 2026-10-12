"""Where a project's work lands: 'here' (eki merges on the local branch) or 'github' (pull requests).

`projects.lands` says it; NULL is a project that never said, and keeps today's rule (a row
with `repo` set is on GitHub, the rest local). GitHub is an option, never required: a project is
a git folder. Switching to GitHub adds or makes the origin through eki/github.py (gh and git only,
never a token). `mark` is the one writer: the row, and the `merge` of the project's workflow
card when it has one. A folder with no commit yet gets its branch from `tick` once it has one.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from . import components, db, github, paths, projectchats, projects, workspace

LANDS = ("here", "github")
#: available()'s why, as what to do about it
FIX = {"gh isn't installed": "gh isn't installed: get it from https://cli.github.com",
       "gh is not logged in": "gh is not logged in: run `gh auth login`"}


def _signed_in() -> tuple:
    ok, why = github.available()
    return ok, "" if ok else FIX.get(why, why)


def _git_folder(folder: Optional[str]) -> Optional[Path]:
    if not folder:
        return None
    where = Path(folder)
    if folder == "~" or folder.startswith("~/"):
        where = paths.user_home() / folder[2:]
    return where if where.is_dir() and workspace.repo_of(where) is not None else None


def _head(where: Path) -> str:
    """The folder's branch when HEAD has a commit, else ''."""
    if not workspace.git(where, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        return ""
    return workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False)


def status(folder: Optional[str] = None) -> dict:
    """gh as the window needs it: signed in, as whom, their repositories, the folder's GitHub origin."""
    ok, why = _signed_in()
    login, repos = "", []
    if ok:
        try:
            login = github.whoami()
            repos = github.repo_list()
        except github.GhError as e:
            why = f"couldn't list your GitHub repositories: {e}"
    where = _git_folder(folder)
    return {"signed_in": ok, "login": login, "why": why, "repos": repos,
            "remote": github.repo_of(where) if where is not None else None}


def _row(conn: sqlite3.Connection, pid: str) -> sqlite3.Row:
    row = projects.get(conn, pid)
    if row is None:
        raise KeyError(f"no project {pid}")
    return row


def _folder(conn: sqlite3.Connection, row: sqlite3.Row) -> Path:
    """The project's own folder: where its card lives and its origin is read."""
    return Path(projectchats.folder_of(conn, row["id"]) or row["path"])


def lands_of(conn: sqlite3.Connection, pid: str) -> dict:
    row = _row(conn, pid)
    where = _folder(conn, row)
    remote = github.repo_of(where) if where.is_dir() else None
    ok, why = _signed_in()
    lands = "github" if row["repo"] or row["lands"] == "github" else "here"
    if ok and lands == "here" and remote is None:
        why = "the folder has no GitHub remote yet: name a repository or create one"
    return {"lands": lands, "repo": row["repo"], "remote": remote, "signed_in": ok, "can_github": ok, "why": why}


def _taken(conn: sqlite3.Connection, pid: str, repo: str) -> None:
    other = projects.by_repo(conn, repo)
    if other is not None and other["id"] != pid:
        raise ValueError(f"{repo} is already the project {other['name']} ({other['id']})")


def _card_merge(where: Path, lands: str) -> None:
    """Only the workflow card's `merge`: 'pr' for github; 'auto' for here when it was 'pr'."""
    found, _ = components.own(where)
    card = next((c for c in found.values() if c.kind == "workflow" and c.card is None), None)
    if card is None:
        return
    f = Path(card.folder) / "component.json"
    spec = json.loads(f.read_text())
    merge = "pr" if lands == "github" else ("auto" if spec.get("merge") == "pr" else spec.get("merge"))
    if merge is None or merge == spec.get("merge"):
        return
    spec["merge"] = merge
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(f)


def mark(conn: sqlite3.Connection, pid: str, lands: str, repo: Optional[str] = None) -> None:
    """The one writer of where a project's work lands."""
    if lands not in LANDS:
        raise ValueError(f"work lands {' or '.join(LANDS)}, not {lands!r}")
    row = _row(conn, pid)
    where = _folder(conn, row)
    if lands == "github":
        if not repo:
            raise ValueError("say which GitHub repository (OWNER/REPO)")
        _taken(conn, pid, repo)
        branch = _head(where) if where.is_dir() else ""
        with db.tx(conn):                                # folder kept: a row with repo reads its folder from it
            conn.execute("UPDATE projects SET lands='github', repo=?, folder=COALESCE(folder, ?), branch=?, "
                         "setup=? WHERE id=?", (repo, str(where), branch, "ready" if branch else row["setup"], pid))
    else:
        home = Path(projectchats.norm(paths.projects()))
        at = Path(projectchats.norm(row["path"]))
        if at == home or home in at.parents:
            raise ValueError(f"{row['path']} is eki's own clone of {row['repo'] or 'a GitHub repository'}: "
                             "its work goes back as pull requests")
        with db.tx(conn):
            conn.execute("UPDATE projects SET lands='here', repo=NULL WHERE id=?", (pid,))
    if where.is_dir():
        _card_merge(where, lands)


def set_lands(conn: sqlite3.Connection, pid: str, lands: str, repo: Optional[str] = None,
              create: bool = False, private: bool = True) -> dict:
    """Say where the work lands; on GitHub, the origin used, added or made first."""
    if lands == "here":
        mark(conn, pid, "here")
        return lands_of(conn, pid)
    if lands != "github":
        raise ValueError(f"work lands {' or '.join(LANDS)}, not {lands!r}")
    row = _row(conn, pid)
    where = _folder(conn, row)
    if _git_folder(str(where)) is None:
        raise ValueError(f"{where} is not a git folder")
    ok, why = _signed_in()
    if not ok:
        raise ValueError(f"pull requests need gh — {why}")
    origin = workspace.git(where, "config", "--get", "remote.origin.url", check=False)
    try:
        if origin:
            got = github.parse(origin)
            if got is None:
                raise ValueError(f"the folder's origin ({origin}) isn't on GitHub")
            if repo and github.named(repo) != got:
                raise ValueError(f"the folder's origin is {got}, not {repo}")
            repo = got
        elif repo:
            got = github.named(repo)
            if got is None:
                raise ValueError(f"{repo!r} isn't OWNER/REPO or a GitHub URL")
            _taken(conn, pid, got)
            github.remote_add(where, got)
            repo = got
        elif create:
            repo = github.repo_create(where.name, where, private)
        else:
            raise ValueError("the folder has no GitHub remote: name a repository (OWNER/REPO) or create one")
    except github.GhError as e:
        raise ValueError(str(e)) from None
    mark(conn, pid, "github", repo)
    return lands_of(conn, pid)


def tick(conn: sqlite3.Connection) -> List[str]:
    """Rows that said where they land before their folder had a commit: the branch, once it has one."""
    said: List[str] = []
    for p in conn.execute("SELECT * FROM projects WHERE lands IS NOT NULL AND COALESCE(branch,'')='' "
                          "AND COALESCE(state,'on') NOT IN ('retired','dropped','merged','deleting','deleted') "
                          "ORDER BY created_at").fetchall():
        where = _folder(conn, p)
        branch = _head(where) if where.is_dir() else ""
        if not branch:
            continue
        with db.tx(conn):
            conn.execute("UPDATE projects SET branch=?, setup=CASE WHEN repo IS NULL THEN setup ELSE 'ready' END "
                         "WHERE id=?", (branch, p["id"]))
        said.append(f"{p['name']}: first commit — branch {branch}" + (f", ready for {p['repo']}" if p["repo"] else ""))
    return said
