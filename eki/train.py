"""The train: integration main goes live every few minutes (docs/self-build.md,
"Replacing the running engine").

Every self.release_minutes the engine asks: is integration main ahead of the
build that runs, is nothing swapping, and was every landed item's gate 2
green? Then main becomes a build; unless it carries only docs, the build
passes the full check first (eki/traincheck.py — red reverts the newest item
and the next tick tries again); `current` points at it, and the launcher
does the rest. Everything landed since the last train goes live together.
`settle` watches the swap record afterwards: the items a build carried are
'live' once it's healthy (and the score before it is written down for gate 4,
eki/score.py), 'rolled back' if the launcher went back from it.

Nothing is kept in memory but the time of the last check; losing it on a
restart only means one early check. What a train carried is the items'
`build` column, what came of it is builds.status().
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import builds, db, score, selfwork, store, traincheck

log = logging.getLogger("eki.train")

RELEASE_MINUTES = 5
_last_check = [0.0]


def release(conn: sqlite3.Connection) -> List[str]:
    """One train now, whatever the setting says (`eki self release`)."""
    return tick(conn, force=True)


def tick(conn: sqlite3.Connection, *, force: bool = False) -> List[str]:
    said = settle(conn)
    got = selfwork.settings()
    if not force:
        if got.get("autonomy") != "apply":
            return said
        every = float(got.get("release_minutes", RELEASE_MINUTES)) * 60
        if time.time() - _last_check[0] < every:
            return said
    _last_check[0] = time.time()
    return said + _go(conn)


def _go(conn: sqlite3.Connection) -> List[str]:
    from . import integration
    landed = conn.execute("SELECT * FROM items WHERE state='landed' ORDER BY landed_at, updated_at").fetchall()
    waiting = [it for it in landed if not it["build"]]
    running = running_commit()
    if running is None and not waiting:
        return []                                     # dev mode, nothing landed: nothing to ship
    main = integration.main()
    if main == running:
        return []
    if running is not None and not integration.contains(running, "main"):
        return [f"train: the running build {running[:12]} isn't in integration main {main[:12]}; not going"]
    swap = builds.status().get("swap") or {}
    if swap.get("state") == "swapping":
        return [f"train: a swap to {Path(swap.get('target') or '?').name} is still under way; waiting"]
    red = [it["id"] for it in landed if it["gate2"] != "green"]
    if red:
        return [f"train: waiting — gate 2 isn't green for {', '.join(red)}"]
    build = traincheck.pending(conn) or builds.make(integration.repo(), "main")
    sha = _commit_of(build) or main
    carried = [it for it in waiting if integration.contains(it["rebased"] or it["commit_sha"] or "", sha)]
    if traincheck.needed(carried):
        green, said = traincheck.step(conn, build, carried)
        if not green:
            _last_check[0] = 0.0                      # the result, or the next train, is up next tick
            return said
    else:
        said = []
    builds.swap_to(build, why=f"train: {len(carried)} item(s)")
    with db.tx(conn):
        for it in carried:
            conn.execute("UPDATE items SET build=?, updated_at=? WHERE id=?", (build.name, db.now(), it["id"]))
    said += [f"train: build {build.name} ({sha[:12]}) is going live with {len(carried)} item(s)"]
    said += [f"item {it['id']}: on the train in build {build.name}" for it in carried]
    return said


def running_commit() -> Optional[str]:
    """The commit of the build that runs; in dev mode that of `current`, else None."""
    sha = _commit_of(builds.running())
    if sha is None:
        cur = builds.current()
        sha = _commit_of(cur) if cur else None
    return sha


def _commit_of(folder: Path) -> Optional[str]:
    info = Path(folder) / ".eki-build.json"
    try:
        sha = json.loads(info.read_text()).get("commit")
    except (OSError, ValueError):
        return None
    return sha if sha and sha != "worktree" else None


# ---- afterwards -------------------------------------------------------------------------

def settle(conn: sqlite3.Connection) -> List[str]:
    """Landed items whose build went live or was rolled back say so."""
    items = conn.execute("SELECT * FROM items WHERE state='landed' AND build IS NOT NULL").fetchall()
    if not items:
        return []
    st = builds.status()
    swap, back = st.get("swap") or {}, st.get("rollback") or {}
    healthy = {Path(b["path"]).name for b in st["builds"] if b.get("healthy")}
    said, went_live = [], {}
    for it in items:
        path = _resolved(builds.root() / it["build"])
        if back and _resolved(back.get("from")) == path and _at(back) >= int(_at(swap)):
            why = f"rolled back from build {it['build']}: exit {back.get('exit')} after {back.get('after')}s"
            _mark(conn, it, "rolled back", why, error=why)
            said.append(f"item {it['id']}: {why}")
        elif it["build"] in healthy or (_resolved(swap.get("target")) == path and swap.get("state") == "healthy"):
            _mark(conn, it, "live", f"live in build {it['build']}")
            said.append(f"item {it['id']}: live in build {it['build']}")
            went_live[it["build"]] = swap.get("healthy_at") if _resolved(swap.get("target")) == path else None
    for name, swapped_at in went_live.items():
        _score(conn, name, swapped_at)
    return said


def _score(conn: sqlite3.Connection, name: str, swapped_at: Any) -> None:
    """Gate 4's 'before': written once per build (record_build is idempotent).
    A failure here is logged; it never holds a build or its items."""
    try:
        at = score.healthy_at(name) or float(swapped_at or 0) or time.time()
        score.record_build(conn, name, at)
    except Exception:
        log.exception("train: couldn't record the score for build %s", name)


def _mark(conn: sqlite3.Connection, it: sqlite3.Row, state: str, text: str, **more: Any) -> None:
    with db.tx(conn):
        now = conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()
        if now is None or now["state"] != "landed":
            return
        conn.execute("UPDATE items SET state=?, error=?, updated_at=? WHERE id=?",
                     (state, more.get("error"), db.now(), it["id"]))
        if it["run_id"]:
            store.add_event(conn, it["run_id"], 0, "note", {"text": text})


def _resolved(p: Any) -> Optional[str]:
    return str(Path(p).resolve()) if p else None


def _at(record: Dict[str, Any]) -> int:
    try:
        return int(float(record.get("at") or 0))
    except (TypeError, ValueError):
        return 0
