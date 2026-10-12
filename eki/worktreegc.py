"""Worktrees that are done with go: housekeeping removes them a few at a time.

Every folder under EKI_HOME/work.noindex (but the dot-folders) is looked at each pass
and removed when nothing will need it again (docs/self-build.md: worktrees):

- an item's worktree (its folder is the item id, or items.worktree says so) once
  the item is live, applied or dropped, or landed with its commit in main — its
  branch goes too, unless the item has an open pull request;
- an item that ended badly (left, unfit, rolled back) once it is KEEP_FAILED_DAYS
  old, so the person can look first — its branch is kept;
- plan-<gid> and draft-<gid> once the goal is past drafting and planning — branch too;
- gate2-<iid> and train-* once nothing is checking in them;
- a folder no row knows of, KEEP_FAILED_DAYS after it was last changed.

Never one an active run is in (an item's run, gate-2 run or review run, or any
active run's thread folder), nor an item's that is building, judging,
reviewing, queued, resolving or rechecking. A removed item keeps its row and
its `worktree` column: retry() takes a missing folder as starting afresh.

A restart loses nothing: before a removal starts, what it will do is written to
work.noindex/.gc/<key>.json, and the next pass finishes any it finds there (rmtree,
`git worktree prune`, branch delete — each harmless twice). The disk the
folders take is measured at most every MEASURE_EVERY seconds, on a thread, into
work.noindex/.disk.json; `disk_line` reads it for `eki self`.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, paths, projects, projectwork, store, workspace

log = logging.getLogger("eki.worktreegc")

#: at most this many removals a pass
BATCH = 5
#: a worktree of an item that ended badly (or a folder no row knows) is kept this long
KEEP_FAILED_DAYS = 2
#: a gate or train tree younger than this is never touched: it may be about to be used
GRACE = 600.0
#: how often the disk the worktrees take is measured
MEASURE_EVERY = 600.0

DONE = ("live", "applied", "dropped")
FAILED = ("left", "unfit", "rolled back")
#: an item in one of these may still be using its worktree
BUSY = ("building", "judging", "reviewing", "queued", "resolving", "rechecking", "waiting on calls")
#: a goal in one of these still uses its plan- / draft- tree
GOAL_BUSY = ("drafting", "planning")

#: the measuring thread, while one runs (in memory only: a restart just measures again)
_thread: List[Optional[threading.Thread]] = [None]


def _pending() -> Path:
    return paths.work() / ".gc"


def _disk_file() -> Path:
    return paths.work() / ".disk.json"


# ---- the pass ---------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said = _finish_pending()
    seen = _rows(conn)
    found = []
    for d in paths.work().iterdir():
        if d.name.startswith(".") or not d.is_dir() or d.is_symlink():
            continue
        try:
            got = _verdict(conn, d, seen)
        except Exception as e:                          # noqa: BLE001 — one odd folder never stops the rest
            log.info("worktree %s: not judged (%s)", d.name, e)
            continue
        if got is not None:
            found.append((d.lstat().st_mtime, d, got))
    for _, d, (why, branch, repo) in sorted(found, key=lambda f: f[0])[:max(0, BATCH - len(said))]:
        rec = {"key": d.name, "why": why, "branch": branch,
               "repo": str(_repo_of(d) or repo or "") or None}
        _pending().mkdir(exist_ok=True)
        (_pending() / f"{d.name}.json").write_text(json.dumps(rec))
        said.append(_finish(rec))
    _measure_maybe()
    return said


def _finish_pending() -> List[str]:
    """Removals a killed pass started: finished now."""
    said = []
    if not _pending().is_dir():
        return said
    for f in sorted(_pending().glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            f.unlink(missing_ok=True)
            continue
        said.append(_finish(rec))
    return said


def _finish(rec: Dict[str, Any]) -> str:
    """Remove the folder, prune the repo's worktree list, delete the branch, forget the record."""
    key, repo, branch = rec["key"], rec.get("repo"), rec.get("branch")
    dest = paths.work() / key
    if dest.exists() or dest.is_symlink():
        if repo and Path(repo).is_dir() and _repo_of(dest) is not None:
            try:
                workspace.remove(repo, key)
            except (workspace.WorkspaceError, OSError) as e:
                log.info("worktree %s: git didn't remove it (%s)", key, e)
        if dest.exists() or dest.is_symlink():          # .git gone or broken, or git failed
            shutil.rmtree(dest, ignore_errors=True)
    if repo and Path(repo).is_dir():
        workspace.git(repo, "worktree", "prune", check=False)
        if branch and workspace.git(repo, "branch", "--list", branch, check=False):
            workspace.git(repo, "branch", "-D", branch, check=False)
    (paths.work() / ".marks" / key).unlink(missing_ok=True)
    (_pending() / f"{key}.json").unlink(missing_ok=True)
    return f"worktree {key}: removed ({rec.get('why') or 'done with'})"


# ---- what may go ------------------------------------------------------------------------------

def _rows(conn: sqlite3.Connection) -> Dict[str, Any]:
    marks = ",".join("?" * len(store.ACTIVE))
    active = {r["id"] for r in conn.execute(f"SELECT id FROM runs WHERE state IN ({marks})", store.ACTIVE)}
    cwds = [r["cwd"] for r in conn.execute(
        f"SELECT t.cwd FROM runs r JOIN threads t ON t.id=r.thread_id WHERE r.state IN ({marks})"
        " AND t.cwd IS NOT NULL", store.ACTIVE)]
    items = conn.execute("SELECT * FROM items").fetchall()
    return {"active": active, "cwds": [os.path.realpath(c) for c in cwds],
            "items": {it["id"]: it for it in items},
            "by_path": {os.path.realpath(it["worktree"]): it for it in items if it["worktree"]}}


def _verdict(conn: sqlite3.Connection, d: Path, seen: Dict[str, Any]):
    """(why, branch to delete or None, repo to fall back on or None) when `d` may go; else None."""
    real = os.path.realpath(d)
    if any(c == real or c.startswith(real + os.sep) for c in seen["cwds"]):
        return None                                     # an active run is working in it
    key, age = d.name, time.time() - d.lstat().st_mtime
    it = seen["items"].get(key) or seen["by_path"].get(real)
    if it is not None:
        return _item_verdict(conn, it, seen["active"], _repo_of(d))
    for prefix in ("plan-", "draft-"):
        if key.startswith(prefix):
            g = conn.execute("SELECT * FROM goals WHERE id=?", (key[len(prefix):],)).fetchone()
            if g is None:
                break
            if g["state"] in GOAL_BUSY or {g["plan_run"], g["draft_run"]} & seen["active"]:
                return None
            return f"goal {g['state']}", f"eki/{key}", _goal_repo(conn, g)
    if key.startswith("gate2-") and seen["items"].get(key[6:]) is not None:
        it = seen["items"][key[6:]]
        if age < GRACE or it["state"] in BUSY or it["gate2_run"] in seen["active"]:
            return None
        return f"gate 2 done, item {it['state']}", None, _item_repo(conn, it)
    if key.startswith(("gate2-", "train-")):
        return ("no check running in it", None, _own_repo()) if age > GRACE else None
    if age > KEEP_FAILED_DAYS * 86400:
        return f"no row knows it, untouched {int(age // 86400)} days", None, _own_repo()
    return None


def _item_verdict(conn: sqlite3.Connection, it: sqlite3.Row, active: set, repo: Optional[Path]):
    if it["state"] in BUSY or {it["run_id"], it["gate2_run"], it["review_run"]} & active:
        return None
    branch = it["branch"] or f"eki/{it['id']}"
    keep_branch = it["pr"] and it["pr_state"] == "open"
    repo = repo or _item_repo(conn, it)
    if it["state"] in DONE or (it["state"] == "landed" and _in_main(conn, it, repo)):
        return f"item {it['state']}", None if keep_branch else branch, repo
    if it["state"] in FAILED and time.time() - it["updated_at"] > KEEP_FAILED_DAYS * 86400:
        return f"item {it['state']} {KEEP_FAILED_DAYS}+ days ago", None, repo
    return None


def _in_main(conn: sqlite3.Connection, it: sqlite3.Row, repo: Optional[Path]) -> bool:
    """Is the item's commit (rebased, or as built) in main — the project's branch for a project's item?"""
    if repo is None:
        return False
    g = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    ref = "main"
    if g is not None and g["project"]:
        p = projects.get(conn, g["project"])
        ref = (it["landed_on"] or (p["branch"] if p is not None else None)) or "main"
    tip = workspace.git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{ref}^{{commit}}", check=False)
    return any(projectwork.is_ancestor(repo, sha, tip) for sha in (it["rebased"], it["commit_sha"]) if sha)


def _item_repo(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[Path]:
    g = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    return _goal_repo(conn, g) if g is not None else _own_repo()


def _goal_repo(conn: sqlite3.Connection, g: sqlite3.Row) -> Optional[Path]:
    if not g["project"]:
        return _own_repo()
    try:
        return projects.repo_for(conn, g)
    except KeyError:
        return None


def _own_repo() -> Optional[Path]:
    """eki's integration repo — only when it is there already: housekeeping never makes it."""
    dest = paths.home() / "self" / "repo"
    if not (dest / ".git").exists():
        return None
    from . import selfwork
    return selfwork.repo()


def _repo_of(d: Path) -> Optional[Path]:
    """The repo whose worktree `d` is, asked of git; None when its .git is gone or broken."""
    if not (d / ".git").exists():
        return None
    try:
        top = workspace.git(d, "rev-parse", "--show-toplevel")
        common = workspace.git(d, "rev-parse", "--git-common-dir")
    except (workspace.WorkspaceError, OSError):
        return None
    if os.path.realpath(top) != os.path.realpath(d):
        return None                                     # git found some repo above it, not its own
    return Path(d, common).resolve().parent


# ---- disk ---------------------------------------------------------------------------------------

def _measure_maybe() -> None:
    try:
        at = float(json.loads(_disk_file().read_text()).get("at") or 0)
    except (OSError, ValueError, AttributeError):
        at = 0.0
    if time.time() - at < MEASURE_EVERY or (_thread[0] is not None and _thread[0].is_alive()):
        return
    t = threading.Thread(target=measure, args=(paths.work(),), name="eki-worktree-disk", daemon=True)
    _thread[0] = t
    t.start()


def measure(work: Path) -> Dict[str, Any]:
    """Count the worktrees and the blocks they really hold (links not followed) into .disk.json."""
    count, total = 0, 0
    try:
        for d in work.iterdir():
            if d.name.startswith(".") or d.is_symlink() or not d.is_dir():
                continue
            count += 1
            total += _blocks(d)
        rec = {"count": count, "bytes": total, "at": time.time()}
        tmp = work / ".disk.json.tmp"
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, work / ".disk.json")
        return rec
    except OSError as e:
        log.info("worktrees: not measured (%s)", e)
        return {}


def _blocks(top: Path) -> int:
    total, stack = 0, [str(top)]
    while stack:
        here = stack.pop()
        try:
            total += os.lstat(here).st_blocks * 512
            with os.scandir(here) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_blocks * 512
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def disk_line() -> str:
    try:
        rec = json.loads(_disk_file().read_text())
        count, size, at = int(rec["count"]), int(rec["bytes"]), float(rec["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return "worktrees: not measured yet"
    return f"worktrees: {count} folders, {_size(size)} on disk (measured {_ago(at)})"


def _size(n: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} B"


def _ago(t: float) -> str:
    s = max(0, int(db.now() - t))
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if s >= n:
            return f"{s // n} {unit} ago"
    return "just now"
