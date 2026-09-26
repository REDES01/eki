"""The train's check: the build about to go live passes the full bin/check and
eki.candidate first (docs/self-build.md, "Judging", gate 3).

Gate 2 judges each item with the fast tests only; the drills and the candidate
checks run here, once per train, on the very build that would be swapped to.
The check is one provider='command' run with cwd=the build. Its result is
written beside the build's .eki-build.json as `.checked`:

    {"state": "running"|"green"|"red", "run": rid, "tail": str, "at": float}

so a restart mid-check finds the run again (or runs it again when it's gone).
Red: the newest item the build carries is reverted on integration main as a new
commit (main was pushed, so no reset), pushed, and marked 'unfit'; the next
train builds the new main and checks it again with the remaining items.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import builds, db, doccheck, store, workspace

log = logging.getLogger("eki.traincheck")

CHECKED = ".checked"
TAIL_LINES = 25
SCRIPT = 'bin/check -q && PYTHONPATH=$PWD "$EKI_PYTHON" -m eki.candidate'


def status(build: Path) -> Optional[Dict[str, Any]]:
    try:
        got = json.loads((Path(build) / CHECKED).read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def label(build: Path) -> str:
    state = (status(build) or {}).get("state")
    return {"green": "checked", "red": "check red", "running": "checking"}.get(state, "-")


def needed(carried: List[sqlite3.Row]) -> bool:
    """False when every carried item is docs-only; a train carrying nothing is checked."""
    return not carried or not all(doccheck.of_item(it) for it in carried)


def pending(conn: sqlite3.Connection) -> Optional[Path]:
    """A build whose check is still running: the train waits for it rather than
    start another check each time main moves under it."""
    for info in builds.root().glob(f"*/{CHECKED}"):
        rec = status(info.parent) or {}
        run = store.run(conn, rec["run"]) if rec.get("state") == "running" and rec.get("run") else None
        if run is not None and run["state"] in store.ACTIVE:
            return info.parent
    return None


def _python() -> str:
    py = str(builds.source() / ".venv" / "bin" / "python")
    return py if os.access(py, os.X_OK) else builds.python()


def command(build: Path) -> str:
    """The check's prompt, run with cwd=build (a build has no .venv of its own)."""
    return json.dumps(["/bin/sh", "-c", f"EKI_PYTHON={shlex.quote(_python())}; export EKI_PYTHON; {SCRIPT}"])


def _write(build: Path, **rec: Any) -> Dict[str, Any]:
    rec.setdefault("tail", "")
    rec["at"] = time.time()
    tmp = Path(build) / f"{CHECKED}.tmp"
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, Path(build) / CHECKED)
    return rec


def _start(conn: sqlite3.Connection, build: Path) -> str:
    with db.tx(conn):
        tid = store.create_thread(conn, f"train check: build {build.name}", str(build))
        rid = store.create_run(conn, tid, command(build), provider="command", priority="now")
    _write(build, state="running", run=rid)
    return rid


def step(conn: sqlite3.Connection, build: Path,
         carried: List[sqlite3.Row]) -> Tuple[Optional[bool], List[str]]:
    """True: green, swap. None: still checking. False: red, and handled."""
    rec = status(build) or {}
    if rec.get("state") == "green":
        return True, []
    if rec.get("state") == "red":
        return False, _red(conn, build, rec, carried)
    run = store.run(conn, rec["run"]) if rec.get("run") else None
    if run is None or run["state"] not in store.ACTIVE + ("done", "failed", "cancelled"):
        rid = _start(conn, build)
        return None, [f"train: checking build {build.name} ({rid[:8]})"]
    if run["state"] in store.ACTIVE:
        return None, [f"train: checking build {build.name} ({run['id'][:8]})"]
    if run["state"] == "done":
        _write(build, state="green", run=run["id"])
        return True, [f"train: build {build.name} passed its check"]
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-TAIL_LINES:])
    tail = (tail + f"\n(train check {run['state']}: {run['error'] or ''})").strip()
    rec = _write(build, state="red", run=run["id"], tail=tail)
    return False, _red(conn, build, rec, carried)


# ---- red: the newest item goes -------------------------------------------------------------

def _red(conn: sqlite3.Connection, build: Path, rec: Dict[str, Any],
         carried: List[sqlite3.Row]) -> List[str]:
    """Revert the newest carried item. Which one is written down first, so a kill
    anywhere in here comes back to the same item (the revert is idempotent)."""
    from . import integration
    if rec.get("reverted"):
        it = conn.execute("SELECT * FROM items WHERE id=?", (rec["reverted"],)).fetchone()
    else:
        newest = sorted(carried, key=lambda it: (it["landed_at"] or 0, it["updated_at"] or 0))[-1:]
        it = newest[0] if newest else None
        if it is not None:
            _write(build, **{**rec, "reverted": it["id"]})
    if it is None:
        return [f"train: build {build.name} failed its check and carries no item to revert"]
    try:
        _revert(it["rebased"] or it["commit_sha"], f"revert-{it['id']}")
    except (integration.IntegrationError, workspace.WorkspaceError) as e:
        log.warning("train: revert of item %s failed: %s", it["id"], e)
        return [f"train: build {build.name} failed its check; reverting item {it['id']} failed "
                f"({e}); tried again next tick"]
    _unfit(conn, it, rec.get("tail") or "")
    return [f"train: build {build.name} failed its check; item {it['id']} reverted"]


def _revert(sha: str, key: str) -> None:
    """`git revert` of `sha` on integration main as a new commit, landed and pushed.
    Nothing happens when main already carries a revert of it."""
    from . import integration, rebase
    repo = integration.repo()
    if workspace.git(repo, "log", "--format=%H", f"--grep=This reverts commit {sha}", "main"):
        return
    tree = rebase.gate_tree(repo, key, integration.main())
    try:
        workspace.git(tree, "revert", "--no-edit", sha)
        integration.fast_forward(workspace.head(tree))
    finally:
        workspace.remove(repo, key)
    integration.push()
    integration.update_source()


def _unfit(conn: sqlite3.Connection, it: sqlite3.Row, tail: str) -> None:
    with db.tx(conn):
        now = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
        if now is None or now["state"] != "landed":
            return
        conn.execute("UPDATE items SET state='unfit', error=?, verdict=?, updated_at=? WHERE id=?",
                     ("the train's check failed", tail[-800:], db.now(), it["id"]))
        if it["run_id"]:
            store.add_event(conn, it["run_id"], 0, "note",
                            {"text": f"reverted: the train's check failed\n{tail}"})
