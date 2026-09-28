"""eki's own clone of a GitHub repo: made, guessed, installed, retired into, dropped.

A GitHub project lives in `~/.eki/projects/<owner>/<repo>` (docs/design.md,
"GitHub is the front"). `ensure` writes the row and starts the clone as a
command run; the housekeeping pass (`tick`) settles what ended: the clone
lands, the branch and the guessed check (eki/checkguess.py) are recorded
once, the install runs once in the clone, and the project turns `ready` — or
`fault`, which only `retry` (`eki project <owner>/<repo> --retry`) starts
again. Nothing waits on a clone: the run is idempotent, so a restart just runs
it again, and every state lives in the projects row.

The same pass retires a person's own folder rows whose origin is on GitHub:
what they started finishes there, nothing new starts, and their standing
goals move to eki's clone once it is ready.
"""
from __future__ import annotations

import json
import shlex
import shutil
import sqlite3
from pathlib import Path
from typing import List, Optional, Set

from . import checkguess, db, github, paths, projects, selfwork, standing, store, workspace

UNFINISHED = ("waiting", "building", "judging", "reviewing")
PLANNING = ("drafting", "planning")
SETTLING = ("cloning", "installing")


# ---- in ---------------------------------------------------------------------------------------

def ensure(conn: sqlite3.Connection, repo: str) -> sqlite3.Row:
    """The project for OWNER/REPO; a new or dropped one is (re)made and its clone started."""
    got = projects.by_repo(conn, repo)
    if got is not None and got["state"] != "dropped":
        return got
    dest = projects.clone_path(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with db.tx(conn):
        pid = got["id"] if got is not None else store.new_id()
        if got is None:
            conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state, setup) "
                         "VALUES (?,?,?,'',?,?,'on','cloning')", (pid, repo, str(dest), db.now(), repo))
        else:
            conn.execute("UPDATE projects SET name=?, path=?, branch='', state='on', setup='cloning', fault=NULL, "
                         "check_cmd=NULL, check_from=NULL, install_cmd=NULL WHERE id=?", (repo, str(dest), pid))
        rid = _clone_run(conn, repo, dest)
        conn.execute("UPDATE projects SET setup_run=? WHERE id=?", (rid, pid))
    return projects.get(conn, pid)


def _clone_run(conn: sqlite3.Connection, repo: str, dest: Path) -> str:
    """Blobless (never shallow: base and merged need ancestry); a clone already landed is kept."""
    d, part = shlex.quote(str(dest)), shlex.quote(f"{dest}.part")
    script = (f"rm -rf {part} && {{ [ -d {d}/.git ] || {{ gh repo clone {shlex.quote(repo)} {part} "
              f"-- --filter=blob:none && mv {part} {d}; }}; }}")
    tid = store.create_thread(conn, f"clone {repo}", str(paths.projects()))
    return store.create_run(conn, tid, json.dumps(["/bin/sh", "-c", script]), provider="command")


def _install_run(conn: sqlite3.Connection, project: sqlite3.Row, cmd: str) -> str:
    tid = store.create_thread(conn, f"install {project['repo']}", project["path"])
    return store.create_run(conn, tid, json.dumps(["/bin/sh", "-c", cmd]), provider="command")


# ---- the tick ---------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    """Settle ended clone/install runs, retire folder rows on GitHub, move their standing goals."""
    said: List[str] = []
    marks = ",".join("?" * len(SETTLING))
    for p in conn.execute(f"SELECT * FROM projects WHERE setup IN ({marks}) AND COALESCE(state,'on')='on' "
                          "ORDER BY created_at", SETTLING).fetchall():
        said += _settle(conn, p)
    said += _retire(conn)
    said += _move(conn)
    return said


def _settle(conn: sqlite3.Connection, p: sqlite3.Row) -> List[str]:
    run = store.run(conn, p["setup_run"]) if p["setup_run"] else None
    if run is not None and run["state"] not in store.ENDED:
        return []
    step = "clone" if p["setup"] == "cloning" else "install"
    if run is None or run["state"] != "done":
        why = (_first(run["error"]) or run["state"]) if run is not None else "its run is gone"
        with db.tx(conn):
            conn.execute("UPDATE projects SET setup='fault', fault=? WHERE id=?", (f"{step} failed: {why}", p["id"]))
        return [f"{p['repo']}: {step} failed: {why} — `eki project {p['repo']} --retry`"]
    if step == "install":
        return _ready(conn, p, "installed")
    clone = Path(p["path"])
    if not (clone / ".git").exists():
        with db.tx(conn):
            conn.execute("UPDATE projects SET setup='fault', fault=? WHERE id=?",
                         (f"clone failed: no clone at {clone}", p["id"]))
        return [f"{p['repo']}: clone failed: no clone at {clone}"]
    branch = p["branch"] or workspace.git(clone, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    said = [f"{p['repo']}: cloned into {clone} (branch {branch or '?'})"]
    with db.tx(conn):
        conn.execute("UPDATE projects SET branch=? WHERE id=?", (branch or "", p["id"]))
        if p["check_from"] != "set":
            g = checkguess.guess(clone)
            source = f"guessed: {g.why}" if g.check else f"none: {g.why}"
            conn.execute("UPDATE projects SET check_cmd=?, install_cmd=?, check_from=? WHERE id=?",
                         (g.check, g.install, source, p["id"]))
            said.append(f"{p['repo']}: check {g.check or 'none'} ({source})")
    p = projects.get(conn, p["id"])
    if p["install_cmd"]:
        with db.tx(conn):
            rid = _install_run(conn, p, p["install_cmd"])
            conn.execute("UPDATE projects SET setup='installing', setup_run=? WHERE id=?", (rid, p["id"]))
        return said + [f"{p['repo']}: installing once in the clone: {p['install_cmd']} (run {rid})"]
    return said + _ready(conn, p, "nothing to install")


def _ready(conn: sqlite3.Connection, p: sqlite3.Row, how: str) -> List[str]:
    with db.tx(conn):
        conn.execute("UPDATE projects SET setup='ready', fault=NULL WHERE id=?", (p["id"],))
    github.soon("issues")
    return [f"{p['repo']}: ready ({how})"]


def _retire(conn: sqlite3.Connection) -> List[str]:
    """Folder rows whose origin is on GitHub: retired, so their work finishes and nothing new starts."""
    said: List[str] = []
    home = paths.projects().resolve()
    for p in conn.execute("SELECT * FROM projects WHERE repo IS NULL ORDER BY created_at").fetchall():
        where = Path(p["path"]).expanduser().resolve()
        if where == home or home in where.parents or not where.is_dir():
            continue
        repo = github.repo_of(where)
        if repo is None:
            continue
        with db.tx(conn):
            conn.execute("UPDATE projects SET repo=?, state='retired' WHERE id=?", (repo, p["id"]))
        said.append(f"{p['name']}: retired — what it started finishes in {where}; "
                    f"new work goes to eki's own clone of {repo}")
    return said


def _move(conn: sqlite3.Connection) -> List[str]:
    """Standing goals on retired rows: their repo's clone made, then repointed to it once ready."""
    said: List[str] = []
    rows = conn.execute("SELECT s.id AS sid, s.state AS sstate, p.repo FROM standing s JOIN projects p "
                        "ON p.id=s.project WHERE p.state='retired' AND s.state!='dropped' "
                        "ORDER BY s.created_at").fetchall()
    for r in rows:
        target = projects.by_repo(conn, r["repo"])
        if target is None or target["state"] == "dropped":
            if r["sstate"] != "on":
                continue
            target = ensure(conn, r["repo"])
            said.append(f"{r['repo']}: making eki's own clone for standing goal {r['sid']}")
        if target["setup"] != "ready":
            continue
        with db.tx(conn):
            conn.execute("UPDATE standing SET project=?, why=NULL WHERE id=?", (target["id"], r["sid"]))
        said.append(f"standing {r['sid']}: moved to eki's own clone of {r['repo']}")
    return said


# ---- out --------------------------------------------------------------------------------------

def drop(conn: sqlite3.Connection, repo: str) -> str:
    """Stop all work on OWNER/REPO, remove the clone, and ignore the issues open now; open PRs stay."""
    p = projects.by_repo(conn, repo)
    if p is None:
        raise KeyError(f"no project {repo}")
    pids = [r["id"] for r in conn.execute("SELECT id FROM projects WHERE repo=?", (repo,)).fetchall()]
    marks = ",".join("?" * len(pids))
    dropped, failed = 0, []
    for g in conn.execute(f"SELECT * FROM goals WHERE project IN ({marks}) AND state NOT IN ('left','failed')",
                          pids).fetchall():
        d, f = _stop(conn, g)
        dropped, failed = dropped + d, failed + f
    stands = conn.execute(f"SELECT * FROM standing WHERE project IN ({marks}) AND state!='dropped'",
                          pids).fetchall()
    for st in stands:
        standing.drop(conn, st["id"])
    if p["setup_run"]:
        store.cancel(conn, p["setup_run"])
    removed = _remove(Path(p["path"]))
    ignored, note = _open_numbers(conn, repo, marks, pids)
    with db.tx(conn):
        conn.execute("UPDATE projects SET state='dropped', check_cmd=NULL, check_from=NULL, install_cmd=NULL, "
                     "fault=NULL, ignored=? WHERE id=?", (json.dumps(ignored), p["id"]))
    out = [f"{repo}: dropped — {dropped} item(s) dropped, {len(stands)} standing goal(s) dropped, "
           f"clone {'removed' if removed else 'not removed'}; open PRs are left to you"]
    out.append(f"issues ignored until a new one: {', '.join(f'#{n}' for n in ignored) or 'none'}{note}")
    return "\n".join(out + failed)


def _stop(conn: sqlite3.Connection, g: sqlite3.Row):
    """Drop a goal's unfinished items; cancel it if still planning (as an issue closing does)."""
    marks = ",".join("?" * len(UNFINISHED))
    items = conn.execute(f"SELECT id FROM items WHERE goal_id=? AND state IN ({marks})",
                         (g["id"], *UNFINISHED)).fetchall()
    dropped, failed = 0, []
    for it in items:
        try:
            selfwork.drop(conn, it["id"])
            dropped += 1
        except Exception as e:                           # noqa: BLE001 — one item's trouble stops one item
            failed.append(f"item {it['id']}: couldn't drop: {str(e)[:120]}")
    if g["state"] in PLANNING:
        for rid in (g["draft_run"], g["plan_run"]):
            if rid:
                store.cancel(conn, rid)
        with db.tx(conn):
            conn.execute("UPDATE goals SET state='left', error='project dropped' WHERE id=?", (g["id"],))
    return dropped, failed


def _remove(clone: Path) -> bool:
    """rmtree, only for a folder under ~/.eki/projects."""
    home, where = paths.projects().resolve(), clone.resolve()
    if home not in where.parents:
        return False
    shutil.rmtree(where, ignore_errors=True)
    shutil.rmtree(Path(f"{where}.part"), ignore_errors=True)
    return not where.exists()


def _open_numbers(conn: sqlite3.Connection, repo: str, marks: str, pids: List[str]):
    try:
        found = github.search_issues(github.whoami())
        return sorted({int(i["number"]) for i in found
                       if (i.get("repository") or {}).get("nameWithOwner") == repo}), ""
    except github.GhError as e:
        got: Set[int] = {r[0] for r in conn.execute(
            f"SELECT issue FROM goals WHERE project IN ({marks}) AND issue IS NOT NULL UNION "
            f"SELECT issue FROM standing WHERE project IN ({marks}) AND issue IS NOT NULL", pids + pids)}
        return sorted(got), f" (GitHub didn't answer: {_first(str(e))}; used the issues its goals came from)"


def retry(conn: sqlite3.Connection, repo: str) -> str:
    """Start the failed step (clone or install) again; ValueError unless setup is 'fault'."""
    p = projects.by_repo(conn, repo)
    if p is None or p["state"] == "dropped" or p["setup"] != "fault":
        raise ValueError(f"{repo} has no setup fault to retry")
    install = (p["fault"] or "").startswith("install") and p["install_cmd"]
    Path(p["path"]).parent.mkdir(parents=True, exist_ok=True)
    with db.tx(conn):
        if install:
            rid = _install_run(conn, p, p["install_cmd"])
        else:
            rid = _clone_run(conn, repo, Path(p["path"]))
        conn.execute("UPDATE projects SET setup=?, setup_run=?, fault=NULL WHERE id=?",
                     ("installing" if install else "cloning", rid, p["id"]))
    return f"{repo}: {'install' if install else 'clone'} started again (run {rid})"


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")[:200]
