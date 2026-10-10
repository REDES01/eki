"""Eki.app rebuilt when a live build changes the Mac shell (docs/self-build.md,
"Replacing the running engine").

The installed app (`app_path()`, the source checkout's Eki.app) says which
commit it was built from in its Info.plist (`EkiCommit`, written by
bin/build-mac). Once the running build is healthy, its commit is compared with
that one: when `mac/` or `bin/build-mac` differ (or the app doesn't say), one
provider='command' run builds a new app from the build folder into
EKI_HOME/shell/<build id>/Eki.app, and when it's done the new app takes the old
one's place. A failed build is one journal fault (`ours: False`: the Swift is
guarded by gate 3, not by a fix goal) and that commit isn't tried again. What
is going on is written in EKI_HOME/shell.json:

    {"state": "building"|"installed"|"failed", "commit": sha, "build": id,
     "run": rid, "error": tail|None, "at": float}

so a restart mid-build finds the run again, or starts it again when it's gone.
The window that's open keeps running its old binary; the page offers Restart.
"""
from __future__ import annotations

import json
import logging
import os
import plistlib
import shlex
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import builds, db, observe, paths, store, workspace

log = logging.getLogger("eki.shellbuild")

STATE = "shell.json"
TAIL_LINES = 40
PATHS = ("mac/", "bin/build-mac")
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")

#: (installed, running) last found to need nothing — a cache, so the git diff
#: isn't asked every tick; losing it costs one diff
_quiet: List[Optional[Tuple[Optional[str], str]]] = [None]


def app_path() -> Path:
    """The installed app."""
    return builds.source() / "Eki.app"


def _old() -> Path:
    return app_path().with_name("Eki.app.old")


def commit_of(app: Path) -> Optional[str]:
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as f:
            got = plistlib.load(f).get("EkiCommit")
    except (OSError, plistlib.InvalidFileException, ValueError, AttributeError):
        return None
    return str(got).strip() or None if isinstance(got, str) else None


def installed_commit() -> Optional[str]:
    return commit_of(app_path())


# ---- what changed --------------------------------------------------------------------------

def _is_commit(repo: Path, sha: str) -> bool:
    return bool(workspace.git(repo, "rev-parse", "-q", "--verify", f"{sha}^{{commit}}", check=False))


def _changed(old: Optional[str], new: str) -> Tuple[List[str], str]:
    """The shell files `new` changes from `old`, and why in words ('' when none)."""
    from . import integration
    if not old:
        return ["?"], "installed app has no EkiCommit"
    repo = integration.repo()
    if not _is_commit(repo, new):
        workspace.git(repo, "fetch", "-q", "source", check=False)   # an `eki swap` to a newer source commit
    if not _is_commit(repo, old):
        return ["?"], f"installed app's commit {old[:12]} is not in the repo"
    if not _is_commit(repo, new):
        return [], ""
    files = workspace.git(repo, "diff", "--name-only", old, new, "--", *PATHS).splitlines()
    files = [f for f in files if f.strip()]
    if not files:
        return [], ""
    shown = ", ".join(files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "")
    return files, f"the shell changed since {old[:12]}: {shown}"


def touches_shell(old: Optional[str], new: str) -> bool:
    return bool(_changed(old, new)[0])


# ---- state ---------------------------------------------------------------------------------

def status() -> Optional[Dict[str, Any]]:
    try:
        got = json.loads((paths.home() / STATE).read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _write(**rec: Any) -> Dict[str, Any]:
    rec.setdefault("error", None)
    rec["at"] = time.time()
    tmp = paths.home() / f"{STATE}.tmp"
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, paths.home() / STATE)
    return rec


def _tmp_app(bid: str) -> Path:
    return paths.home() / "shell" / bid / "Eki.app"


def view() -> Dict[str, Any]:
    rec = status() or {}
    return {"installed": installed_commit(), "app": str(app_path()), "state": rec.get("state"),
            "build": rec.get("build"), "run": rec.get("run"), "error": rec.get("error")}


# ---- the engine step -----------------------------------------------------------------------

def _live() -> Optional[Tuple[str, str, Path]]:
    """(build id, commit, folder) of the running build, or None in dev mode."""
    here = builds.running()
    try:
        info = json.loads((here / ".eki-build.json").read_text())
    except (OSError, ValueError):
        return None
    sha = str(info.get("commit") or "")
    if not sha or sha == "worktree":
        return None
    return str(info.get("id") or here.name), sha, here


def _healthy(here: Path) -> bool:
    me = here.resolve()
    return any(b.get("healthy") and Path(b["path"]).resolve() == me for b in builds.status()["builds"])


def _command(bid: str, sha: str) -> str:
    env = (f"EKI_APP_OUT={shlex.quote(str(_tmp_app(bid)))} "
           f"EKI_APP_ROOT={shlex.quote(str(builds.source()))} EKI_APP_COMMIT={shlex.quote(sha)}")
    return json.dumps(["/bin/sh", "-c", f"{env} bin/build-mac"])


def _start(conn: sqlite3.Connection, bid: str, sha: str, here: Path, why: str) -> str:
    shutil.rmtree(_tmp_app(bid).parent, ignore_errors=True)
    with db.tx(conn):
        tid = store.create_thread(conn, f"shell build: build {bid}", str(here))
        rid = store.create_run(conn, tid, _command(bid, sha), provider="command", priority="background")
        store.update_run(conn, rid, why=why)
    _write(state="building", commit=sha, build=bid, run=rid, why=why)
    return rid


def _recover() -> List[str]:
    """A kill mid-swap: never leave the app missing, nor a stale .old beside it."""
    app, old = app_path(), _old()
    if not old.exists():
        return []
    if not app.exists():
        os.rename(old, app)
        return [f"shell: put {app} back after an interrupted swap"]
    shutil.rmtree(old, ignore_errors=True)
    return []


def _swap(new: Path) -> None:
    app, old = app_path(), _old()
    if app.exists():
        os.rename(app, old)
    os.rename(new, app)
    shutil.rmtree(old, ignore_errors=True)
    if Path(LSREGISTER).exists():
        try:
            subprocess.run([LSREGISTER, "-f", str(app)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


def _fail(conn: sqlite3.Connection, rec: Dict[str, Any], run: sqlite3.Row, error: str) -> str:
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-TAIL_LINES:])
    tail = (tail + f"\n({error})").strip()
    observe.record(conn, "fault", run_id=run["id"], data={
        "where": "shell build", "exc": "swiftc", "ours": False, "frame": None, "traceback": None,
        "output": tail, "build": rec.get("build")})
    shutil.rmtree(_tmp_app(str(rec.get("build"))).parent, ignore_errors=True)
    _write(**{**rec, "state": "failed", "error": tail})
    return f"shell: building Eki.app for build {rec.get('build')} failed ({error})"


def _follow(conn: sqlite3.Connection, rec: Dict[str, Any]) -> List[str]:
    """shell.json says building: wait, swap, or write the failure down."""
    bid, sha = str(rec.get("build")), str(rec.get("commit"))
    run = store.run(conn, rec["run"]) if rec.get("run") else None
    if run is None or run["state"] not in store.ACTIVE + ("done", "failed", "cancelled"):
        here = builds.root() / bid
        if not (here / "bin" / "build-mac").exists():
            here = builds.running()
        rid = _start(conn, bid, sha, here, rec.get("why") or "the shell build's run was gone")
        return [f"shell: building Eki.app again for build {bid} ({rid[:8]})"]
    if run["state"] in store.ACTIVE:
        return []
    if run["state"] == "done":
        new = _tmp_app(bid)
        if commit_of(new) == sha:
            _swap(new)
        elif installed_commit() != sha:               # not already swapped by a killed tick
            return [_fail(conn, rec, run, "the build left no Eki.app")]
        shutil.rmtree(new.parent, ignore_errors=True)
        _write(**{**rec, "state": "installed", "error": None})
        return [f"shell: Eki.app from build {bid} installed"]
    return [_fail(conn, rec, run, f"shell build {run['state']}: {run['error'] or ''}".strip())]


def tick(conn: sqlite3.Connection) -> List[str]:
    said = _recover()
    rec = status() or {}
    if rec.get("state") == "building":
        return said + _follow(conn, rec)
    live = _live()
    if live is None:
        return said
    bid, sha, here = live
    if rec.get("commit") == sha and rec.get("state") in ("installed", "failed"):
        return said
    have = installed_commit()
    if have == sha or _quiet[0] == (have, sha) or not _healthy(here):
        return said
    from . import integration
    try:
        files, why = _changed(have, sha)
    except (integration.IntegrationError, workspace.WorkspaceError) as e:
        log.warning("shell: cannot tell what build %s changed: %s", bid, e)
        files, why = [], ""
    if not files:
        _quiet[0] = (have, sha)
        return said
    rid = _start(conn, bid, sha, here, why)
    return said + [f"shell: building Eki.app for build {bid} ({rid[:8]}): {why}"]
