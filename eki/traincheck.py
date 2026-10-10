"""The train's check: the build about to go live passes bin/check and
eki.candidate first (docs/self-build.md, "Judging", gate 3).

Gate 2 judges each item with the fast tests only; the drills and the candidate
checks run here, once per train, on the very build that would be swapped to.
When the build's head is exactly what gate 2 passed — every commit since the
running build is the rebased commit of a landed, gate-2-green item — the fast
tests already ran on it, so only the drills run (scope "drills"); anything else
(a commit by hand, a revert from an earlier red train, no running build, git
unable to say) runs the full suite (scope "full").
The check is one provider='command' run in a worktree of the integration repo
at the build's commit (the exported build has no .git). Its result is
written beside the build's .eki-build.json as `.checked`:

    {"state": "running"|"green"|"red", "run": rid, "scope": "drills"|"full",
     "tail": str, "at": float}

so a restart mid-check finds the run again (or runs it again when it's gone),
with the scope chosen when it first started.
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
DRILLS = 'bin/check -q -m drill && PYTHONPATH=$PWD "$EKI_PYTHON" -m eki.candidate'
SCOPES = {"drills": DRILLS, "full": SCRIPT}


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
    """A build whose check is running, or has ended and isn't judged yet: the train
    finishes that one rather than start another check each time main moves under it.
    (Only waiting for *running* checks threw away every finished one whenever main
    had moved meanwhile, and with main moving every few minutes nothing shipped.)
    A build the running one already carries is stale and is left alone."""
    from . import integration
    running = train_running()
    found = []
    for info in builds.root().glob(f"*/{CHECKED}"):
        rec = status(info.parent) or {}
        run = store.run(conn, rec["run"]) if rec.get("state") == "running" and rec.get("run") else None
        if run is None:
            continue
        sha = _commit(info.parent)
        if running and integration.carries(sha, running):
            continue
        found.append((run["created_at"], info.parent))
    return max(found)[1] if found else None


def train_running() -> Optional[str]:
    from . import train
    return train.running_commit()


def _python() -> str:
    py = str(builds.source() / ".venv" / "bin" / "python")
    return py if os.access(py, os.X_OK) else builds.python()


def command(build: Path, scope: str = "full") -> str:
    """The check's prompt. It runs in a worktree of the integration repo at the
    build's commit — the same ground as gate 2, with a .git and a linked .venv —
    not in the exported build, where tests that ask git about the checkout
    they're in would be asking about the wrong one. The candidate check is
    told which build is running. Scope "drills" runs only what gate 2 skips."""
    running = builds.current() or builds.running()
    return json.dumps(["/bin/sh", "-c", f"EKI_PYTHON={shlex.quote(_python())}; export EKI_PYTHON; "
                       f"{SCOPES.get(scope, SCRIPT)} --running {shlex.quote(str(running))}"])


def tree_for(build: Path) -> Path:
    """The worktree the build's check runs in (made when it isn't there)."""
    from . import integration, rebase
    key = f"train-{build.name}"
    dest = workspace.path_for(key)
    if (dest / ".git").exists():
        return dest
    return rebase.gate_tree(integration.repo(), key, _commit(build))


def judged(conn: sqlite3.Connection, running: Optional[str], sha: str) -> bool:
    """True only when every commit from the running build to `sha` is the rebased
    commit of a landed item gate 2 passed: the fast tests already ran on this head."""
    from . import integration
    if not running or not sha:
        return False
    try:
        commits = workspace.git(integration.repo(), "rev-list", f"{running}..{sha}").split()
    except (workspace.WorkspaceError, OSError) as e:
        log.info("train: can't list %s..%s (%s); full check", running[:12], sha[:12], e)
        return False
    if not commits:
        return False
    marks = ",".join("?" * len(commits))
    passed = {r["rebased"] for r in conn.execute(
        f"SELECT rebased FROM items WHERE state='landed' AND gate2='green' AND rebased IN ({marks})",
        commits)}
    return all(c in passed for c in commits)


def _commit(build: Path) -> str:
    return str(json.loads((Path(build) / ".eki-build.json").read_text())["commit"])


def _done_with(build: Path) -> None:
    from . import integration
    workspace.remove(integration.repo(), f"train-{build.name}")


def _write(build: Path, **rec: Any) -> Dict[str, Any]:
    rec.setdefault("tail", "")
    rec["at"] = time.time()
    tmp = Path(build) / f"{CHECKED}.tmp"
    tmp.write_text(json.dumps(rec, indent=2))
    os.replace(tmp, Path(build) / CHECKED)
    return rec


def _start(conn: sqlite3.Connection, build: Path, scope: str) -> str:
    tree = tree_for(build)
    with db.tx(conn):
        tid = store.create_thread(conn, f"train check: build {build.name}", str(tree), by="eki")
        rid = store.create_run(conn, tid, command(build, scope), provider="command", priority="now")
    _write(build, state="running", run=rid, scope=scope)
    return rid


def step(conn: sqlite3.Connection, build: Path,
         carried: List[sqlite3.Row], running: Optional[str] = None) -> Tuple[Optional[bool], List[str]]:
    """True: green, swap. None: still checking. False: red, and handled.
    `running` is the running build's commit; the scope is decided once, at the start."""
    rec = status(build) or {}
    if rec.get("state") == "green":
        return True, []
    if rec.get("state") == "red":
        return False, _red(conn, build, rec, carried)
    run = store.run(conn, rec["run"]) if rec.get("run") else None
    if run is None or run["state"] not in store.ACTIVE + ("done", "failed", "cancelled"):
        scope = rec.get("scope") or ("drills" if judged(conn, running, _commit(build)) else "full")
        rid = _start(conn, build, scope)
        return None, [f"train: checking build {build.name} ({rid[:8]}, {scope})"]
    if run["state"] in store.ACTIVE:
        return None, [f"train: checking build {build.name} ({run['id'][:8]})"]
    if run["state"] == "done":
        _write(build, state="green", run=run["id"], scope=rec.get("scope") or "full")
        _done_with(build)
        return True, [f"train: build {build.name} passed its check"]
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-TAIL_LINES:])
    tail = (tail + f"\n(train check {run['state']}: {run['error'] or ''})").strip()
    rec = _write(build, state="red", run=run["id"], scope=rec.get("scope") or "full", tail=tail)
    _done_with(build)
    return False, _red(conn, build, rec, carried)


# ---- red: the newest item goes -------------------------------------------------------------

def _red(conn: sqlite3.Connection, build: Path, rec: Dict[str, Any],
         carried: List[sqlite3.Row]) -> List[str]:
    """Revert the newest carried item. Which one is written down first, so a kill
    anywhere in here comes back to the same item (the revert is idempotent)."""
    from . import integration, notify
    if not rec.get("reverted") and _by_hand(conn, build):
        notify.queue(conn, f"train:{build.name}:by-hand", "needs_you", "eki · needs you",
                     f"build {build.name} failed its release check and carries commits made by hand; "
                     f"nothing was reverted — fix main (eki builds)")
        return [f"train: build {build.name} failed its check; it carries commits made by hand, which gate 2 "
                f"never judged, so no item is blamed or reverted — the next main is checked again"]
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


def _by_hand(conn: sqlite3.Connection, build: Path) -> bool:
    """The build carries a commit no gate-2-green item made (a fix pushed by hand):
    then the newest item isn't the likely culprit, and reverting it punishes the wrong one."""
    from . import train
    running = train.running_commit()
    return bool(running) and not judged(conn, running, _commit(build))


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
