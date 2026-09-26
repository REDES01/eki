"""The score, and gate 4: was eki better or worse after a build went live?
(docs/self-build.md, "Judging").

A score is computed from the journal alone (eki/observe.py) for a window of
time: how much a local model finished without handing off, and faults,
corrections and handoffs per 100 runs, and how long the first text took.
When a build goes healthy the train records the score of the window before
it (`record_build`); each housekeeping pass recomputes the window since
(`settle`) until it has LEAST runs, then the verdict is frozen. A 'worse'
build is written to the journal as a 'regression'. Nothing here blocks or
undoes anything by itself: `undo` is only ever called on request, and what
it makes goes the ordinary path.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from typing import Any, Dict, List, Optional

from . import builds, db, observe, selfwork, store

#: runs a window needs before a score is trusted (and an 'after' is frozen)
LEAST = 30
#: a rate is worse when it rose by more than this share of itself …
RELATIVE = 0.5
#: … and by at least this many per 100 runs
ABSOLUTE = 2.0
#: local_share moving by more than this is a change
LOCAL_POINTS = 0.10


def _per100(n: int, runs: int) -> Optional[float]:
    return round(100.0 * n / runs, 3) if runs else None


def compute(conn: sqlite3.Connection, since: float, until: Optional[float] = None) -> Dict[str, Any]:
    """The score over the 'run' rows in [since, until)."""
    runs = observe.entries(conn, since=since, until=until, kind="run")
    n = len(runs)
    finished = [r["data"] for r in runs if r["data"].get("state") in ("done", "handed_off")]
    local = [d for d in finished if d.get("local") and not d.get("handed_off")]
    handed = [r for r in runs if r["data"].get("handed_off")]
    faults = len(observe.entries(conn, since=since, until=until, kind="fault"))
    corrections = len(observe.entries(conn, since=since, until=until, kind="correction"))
    firsts = [r["data"]["first_text"] for r in runs
              if isinstance(r["data"].get("first_text"), (int, float))]
    return {"runs": n,
            "local_share": round(len(local) / len(finished), 4) if n and finished else None,
            "fault_rate": _per100(faults, n),
            "correction_rate": _per100(corrections, n),
            "handoff_rate": _per100(len(handed), n),
            "median_first_text": statistics.median(firsts) if firsts else None,
            "since": since, "until": until}


def last_runs(conn: sqlite3.Connection, n: int, until: float) -> Dict[str, Any]:
    """The score of the window that holds the last `n` runs before `until`."""
    row = conn.execute("SELECT t FROM journal WHERE kind='run' AND t<? ORDER BY t DESC, id DESC"
                       " LIMIT 1 OFFSET ?", (until, max(n - 1, 0))).fetchone()
    if row is None:        # fewer than n: all of them
        row = conn.execute("SELECT MIN(t) FROM journal WHERE kind='run' AND t<?", (until,)).fetchone()
    since = row[0] if row is not None and row[0] is not None else until
    return compute(conn, since, until)


def window(conn: sqlite3.Connection, since: float, until: Optional[float] = None,
           least: int = LEAST) -> Dict[str, Any]:
    got = compute(conn, since, until)
    if got["runs"] < least and until is not None:
        return last_runs(conn, least, until)
    return got


# ---- the verdict ------------------------------------------------------------------------

def _rose(before: Optional[float], after: Optional[float]) -> bool:
    if before is None or after is None:
        return False
    return after - before >= ABSOLUTE and after > before * (1 + RELATIVE)


def verdict(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """'worse' | 'better' | 'same', by a rule simple enough to say in a line."""
    rates = ("fault_rate", "correction_rate")
    lb, la = before.get("local_share"), after.get("local_share")
    local_known = lb is not None and la is not None
    if any(_rose(before.get(k), after.get(k)) for k in rates) or (local_known and lb - la > LOCAL_POINTS):
        return "worse"
    if any(_rose(after.get(k), before.get(k)) for k in rates) or (local_known and la - lb > LOCAL_POINTS):
        return "better"
    return "same"


# ---- builds -----------------------------------------------------------------------------

def healthy_at(build_id: str) -> Optional[float]:
    """When the engine wrote this build's healthy marker."""
    marker = builds.root() / build_id / ".healthy"
    try:
        return float(marker.read_text().strip())
    except ValueError:
        try:
            return marker.stat().st_mtime
        except OSError:
            return None
    except OSError:
        return None


def record_build(conn: sqlite3.Connection, build_id: str, healthy_at: float) -> bool:
    """Write down the score before this build; once per build."""
    if conn.execute("SELECT 1 FROM build_scores WHERE build=?", (build_id,)).fetchone():
        return False
    prev = conn.execute("SELECT MAX(healthy_at) FROM build_scores WHERE healthy_at<?",
                        (healthy_at,)).fetchone()[0]
    before = window(conn, since=prev or 0.0, until=healthy_at)
    cur = conn.execute("INSERT OR IGNORE INTO build_scores(build, healthy_at, before) VALUES (?,?,?)",
                       (build_id, healthy_at, db.dumps(before)))
    return cur.rowcount > 0


def settle(conn: sqlite3.Connection, now: Optional[float] = None) -> List[str]:
    """Bring each open 'after' up to date; freeze it and judge once it has LEAST runs."""
    now = db.now() if now is None else now
    said: List[str] = []
    for row in conn.execute("SELECT * FROM build_scores WHERE verdict IS NULL").fetchall():
        after = compute(conn, row["healthy_at"], now)
        if after["runs"] < LEAST:
            conn.execute("UPDATE build_scores SET after=?, measured_at=? WHERE build=? AND verdict IS NULL",
                         (db.dumps(after), now, row["build"]))
            continue
        before = json.loads(row["before"] or "{}")
        v = verdict(before, after)
        with db.tx(conn):
            cur = conn.execute("UPDATE build_scores SET after=?, measured_at=?, verdict=?"
                               " WHERE build=? AND verdict IS NULL",
                               (db.dumps(after), now, v, row["build"]))
            if cur.rowcount and v == "worse":
                observe.record(conn, "regression", build=row["build"],
                               data={"before": before, "after": after, "verdict": v})
        if cur.rowcount:
            said.append(f"build {row['build']}: judged {v} ({after['runs']} runs since it went healthy)")
    return said


def verdict_of(conn: sqlite3.Connection, build_id: str) -> Optional[str]:
    row = conn.execute("SELECT verdict FROM build_scores WHERE build=?", (build_id,)).fetchone()
    return row["verdict"] if row else None


def worse(conn: sqlite3.Connection, since: float = 0) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM build_scores WHERE verdict='worse' AND measured_at>=?"
                        " ORDER BY measured_at", (since,)).fetchall()


# ---- undo, on request only ---------------------------------------------------------------

def _revert_spec(it: sqlite3.Row, sha: str, build_id: str) -> str:
    return (f"Build {build_id} was judged worse than the one before it; take back what item "
            f"{it['id']} ({it['title']}) changed.\n\n"
            f"On this worktree's base, run `git revert --no-edit {sha}`. If it conflicts, resolve "
            f"it keeping the changes that came later — only this item's change goes. Then run "
            f"the tests (bin/check) and leave them green.")


def undo(conn: sqlite3.Connection, build_id: str) -> str:
    """One goal 'undo build <id>': a revert item for each item the build carried,
    newest landed first, each after the one before. Returns the goal id."""
    carried = conn.execute("SELECT * FROM items WHERE build=? ORDER BY COALESCE(landed_at, updated_at) DESC,"
                           " created_at DESC", (build_id,)).fetchall()
    carried = [it for it in carried if it["rebased"] or it["commit_sha"]]
    if not carried:
        raise ValueError(f"build {build_id} carried no items")
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, f"undo build {build_id}", "ask", "you", "planned", db.now()))
        prev: Optional[str] = None
        for it in carried:
            sha = it["rebased"] or it["commit_sha"]
            prev = selfwork.new_item(conn, gid, f"revert: {it['title']}"[:120], _revert_spec(it, sha, build_id),
                                     json.loads(it["touched"] or "[]"), [prev] if prev else [], False)
    return gid
