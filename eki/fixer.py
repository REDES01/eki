"""One more try before an eki item is left for a person (docs/self-build.md).

Before an eki item becomes `unfit` (gate 1 or gate 2 red) or `left` (a resolve
that failed, a build that ended its turn early with nothing changed), its
caller asks `offer` for a fixer run: in the item's own worktree, put on
integration main, told the failure, what landed since the item's base on its
files and why (eki/landed.py), and the rule — make the intent work with what
landed, never weaken a check. The item is `fixing` meanwhile. `tick` hands a
done fixer's result to selfwork._built, so it is committed and goes through
gate 1 and the review as any build does. One fixer run per item: `items.fixer`
set means it has had it, and a second failure leaves it as before, `tried`
saying what the fixer did.

Everything is in `items.fixer` and the runs; every git step is safe to repeat,
so a restart at any point loses nothing. Only eki's own items get a fixer.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, fixerbrief, integration, landed, rebase, selfbrief, selfwork, store
from .workspace import WorkspaceError, git

STAGES = ("gate1", "gate2", "resolve", "nothing")
#: what `items.fixer.failure` keeps, and what `tried` shows of the fixer's summary
FAILURE_CHARS = 2500
TRIED_CHARS = 300
#: an item now in one of these after its fixer run counts as fixed (`daily`)
FIXED = ("proposed", "queued", "landed", "live", "applied")
_ITEM = re.compile(r"^\s*`?ITEM:\s*(done|partial|person)\b", re.M | re.I)


def of(it: sqlite3.Row) -> Optional[Dict[str, Any]]:
    """The item's `fixer` JSON; None when it has had no fixer run (or it can't be read)."""
    try:
        got = json.loads(it["fixer"] or "null")
    except (IndexError, KeyError, ValueError, TypeError):
        return None
    return got if isinstance(got, dict) else None


# ---- in -----------------------------------------------------------------------------------

def offer(conn: sqlite3.Connection, it: sqlite3.Row, stage: str, failure: str) -> Optional[str]:
    """Under the caller's tx: start the item's one fixer run and return the board line,
    or None — nothing changed — when it gets none."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    if goal is None or goal["project"] is not None or it["fixer"]:
        return None
    if not selfwork.settings().get("fixer", True):
        return None
    wt = it["worktree"]
    if not wt or not Path(wt).is_dir() or not it["thread_id"]:
        return None
    try:
        main = integration.main()
        conflicted = _put_on(it, main)
        files = sorted(set(_files(it)) | set(conflicted))
        intents = landed.intents(conn, it, main, files)      # from the item's old base
        sha = it["commit_sha"]
        if sha and not conflicted:
            sha = git(wt, "rev-parse", "HEAD")
    except (WorkspaceError, OSError, integration.IntegrationError):
        return None
    failure = (failure or "").strip()[-FAILURE_CHARS:]
    prompt = fixerbrief.brief(stage=stage, goal=goal["text"], title=it["title"], spec=it["spec"],
                              summary=it["summary"] or "", failure=failure, head=main,
                              landed=intents, conflicted=conflicted)
    rid = store.create_run(conn, it["thread_id"], prompt, row="code", fresh=True,
                           priority="now" if goal["owner"] == "you" else "background")
    note = {"stage": stage, "failure": failure, "run": rid, "at": db.now(), "head": main, "summary": None}
    selfwork._set(conn, it["id"], state="fixing", run_id=rid, fixer=db.dumps(note), base=main, commit_sha=sha,
                  head=None, rebased=None, resolved_on=None, gate2=None, gate2_run=None, gate2_on=None,
                  error=None)
    return f"item {it['id']}: {stage} failed — the fixer is on it ({rid[:8]})"


def _put_on(it: sqlite3.Row, main: str) -> List[str]:
    """The item's own commits onto `main` in its worktree. [] when done; else the conflicted
    files, the worktree left mid-rebase for the fixer."""
    wt = it["worktree"]
    if rebase.in_progress(wt):
        rebase.abort(wt)
        if it["commit_sha"]:
            git(wt, "reset", "-q", "--hard", it["commit_sha"])
    if _is_ancestor(wt, main, "HEAD"):
        return []
    at = git(wt, "rev-parse", "HEAD")
    since = it["head"] if it["rebased"] and at == it["rebased"] else None   # only its own commits move
    if since is None and it["resolved_on"] and _is_ancestor(wt, it["resolved_on"], at):
        since = it["resolved_on"]
    if since is None and it["base"] and _is_ancestor(wt, it["base"], at):
        since = it["base"]
    return rebase.onto(wt, main, since=since)


def _files(it: sqlite3.Row) -> List[str]:
    """What the item touched, and the plain paths (not globs) it declared."""
    try:
        touched = json.loads(it["touched"] or "[]")
        declared = json.loads(it["files"] or "[]")
    except ValueError:
        return []
    return [f for f in (*touched, *declared) if isinstance(f, str) and f and not any(c in f for c in "*?[")]


def _is_ancestor(where: str, commit: str, of: str) -> bool:
    try:
        git(where, "merge-base", "--is-ancestor", commit, of)
        return True
    except WorkspaceError:
        return False


# ---- reading ----------------------------------------------------------------------------------

def ended_early(answer: str) -> bool:
    """The build stopped short: no ITEM line, or done/partial saying it is waiting. Never for person."""
    if not _ITEM.search(answer or ""):
        return True
    verdict, reason, summary = selfbrief.outcome(answer)
    if verdict is None:
        return True
    if verdict == "person":
        return False
    return "waiting" in f"{reason}\n{summary}".lower()


def tried(it: sqlite3.Row) -> Optional[str]:
    """One line on the item's fixer run, or None when it had none."""
    note = of(it)
    if note is None:
        return None
    if note.get("summary") is None:
        return f"the fixer is working ({note.get('stage')})"
    said = " ".join(str(note["summary"]).split())
    if len(said) > TRIED_CHARS:
        said = said[:TRIED_CHARS - 1].rstrip() + "…"
    return f"the fixer tried ({note.get('stage')}): {said}"


def daily(conn: sqlite3.Connection, days: int = 7) -> List[Dict[str, Any]]:
    """Per local calendar day, newest first: eki's own items the fixer fixed, and those left for you."""
    today = datetime.date.today()
    out = [{"day": (today - datetime.timedelta(days=n)).isoformat(), "fixed": 0, "left": 0} for n in range(days)]
    by = {d["day"]: d for d in out}
    since = time.mktime((today - datetime.timedelta(days=days - 1)).timetuple())
    rows = conn.execute("SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project IS NULL AND "
                        "(i.fixer IS NOT NULL OR i.state IN ('unfit', 'left')) AND i.updated_at>=?",
                        (since,)).fetchall()
    for it in rows:
        if it["state"] in ("unfit", "left"):
            d = by.get(_day(it["updated_at"]))
            if d is not None:
                d["left"] += 1
        note = of(it)
        if it["state"] in FIXED and note is not None and isinstance(note.get("at"), (int, float)):
            d = by.get(_day(note["at"]))
            if d is not None:
                d["fixed"] += 1
    return out


def _day(t: Optional[float]) -> str:
    return datetime.date.fromtimestamp(t).isoformat() if t else ""


# ---- the tick -------------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    said: List[str] = []
    for it in selfwork.items_in(conn, ("fixing",)):
        said += _conclude(conn, it)
    return said


def _still(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    row = conn.execute("SELECT state, run_id FROM items WHERE id=?", (it["id"],)).fetchone()
    return row is not None and row["state"] == "fixing" and row["run_id"] == it["run_id"]


def _conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if run is not None and run["state"] in store.ACTIVE:
        return []
    if run is None or run["state"] != "done":
        return _leave(conn, it, f"the fixer run ended {run['state'] if run else 'missing'}")
    _, _, summary = selfbrief.outcome(store.answer(conn, run["id"]))
    note = {**(of(it) or {}), "summary": summary or "(nothing said)"}
    with db.tx(conn):
        if not _still(conn, it):
            return []
        selfwork._set(conn, it["id"], fixer=db.dumps(note))
    wt = it["worktree"]
    try:
        if rebase.in_progress(wt):
            left = rebase.markers(wt, note.get("head") or it["base"])
            if left:
                return _leave(conn, it, f"the fixer left conflict markers in {', '.join(left)}")
            stopped = rebase.proceed(wt)
            if stopped:
                return _leave(conn, it, f"the rebase stopped again on {', '.join(stopped)}")
        at = git(wt, "rev-parse", "HEAD")
    except (WorkspaceError, OSError) as e:
        return _leave(conn, it, f"the rebase would not go on after the fixer: {e}")
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    with db.tx(conn):
        if not _still(conn, it):
            return []
        if at != it["base"]:                     # its own commits, on main: what is judged if nothing changes
            selfwork._set(conn, it["id"], commit_sha=at)
        said = selfwork._built(conn, selfwork.store_item(conn, it["id"]), run, goal, None)
        selfwork._set(conn, it["id"], summary=it["summary"])      # the builder's stays; the fixer's is in fixer
    return [f"{line} (after the fixer)" for line in said]


def _leave(conn: sqlite3.Connection, it: sqlite3.Row, why: str) -> List[str]:
    """Stop a rebase the fixer was in and leave the item for a person."""
    wt = it["worktree"]
    try:
        if wt and Path(wt).is_dir():
            rebase.abort(wt)
    except WorkspaceError as e:
        why = f"{why} (and {e})"
    cur = selfwork.store_item(conn, it["id"])
    note = of(cur) if cur is not None else None
    with db.tx(conn):
        if not _still(conn, it):
            return []
        fields: Dict[str, Any] = {}
        if note is not None and note.get("summary") is None:    # `tried` says how it ended
            fields["fixer"] = db.dumps({**note, "summary": f"({why})"})
        selfwork._set(conn, it["id"], state="left", error=why, **fields)
    return [f"item {it['id']}: left for you — {why}"]
