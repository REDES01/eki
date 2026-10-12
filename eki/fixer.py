"""One more try before an eki item is left for a person (docs/self-build.md).

Before an eki item becomes `unfit` (gate 1 or gate 2 red) or `left` (a resolve
that failed, a build that ended its turn early with nothing changed), the work
model (eki/workstep.py) gives it one fixer run when the executor says it can
(`can_start`): the start_run fix handler `prepare`s the item's own worktree on
integration main and `launch`es the run, told the failure, what landed since
the item's base on its files and why (eki/landed.py), and the rule — make the
intent work with what landed, never weaken a check. The item is `fixing`
meanwhile. `tick` feeds a done fixer's result as a build's (eki/buildfeed.py),
so it is committed and goes through gate 1 and the review as any build does.
One fixer run per item: `items.fixer` set means it has had it, and a second
failure leaves it as before, `tried` saying what the fixer did.

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
from .workmodel import Event
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

def can_start(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """The executor's facts for a fixer run: `self.fixer` on, the worktree and thread there.
    Whose item gets one, and only once, is the work model's (workstep.can_fix)."""
    wt = it["worktree"]
    return bool(selfwork.settings().get("fixer", True) and wt and Path(wt).is_dir() and it["thread_id"])


def prepare(conn: sqlite3.Connection, it: sqlite3.Row) -> Dict[str, Any]:
    """The item's own commits put on integration main, and what landed meanwhile. Raises
    WorkspaceError, OSError or IntegrationError when git won't."""
    main = integration.main()
    conflicted = _put_on(it, main)
    files = sorted(set(_files(it)) | set(conflicted))
    intents = landed.intents(conn, it, main, files)      # from the item's old base
    sha = it["commit_sha"]
    if sha and not conflicted:
        sha = git(it["worktree"], "rev-parse", "HEAD")
    return {"main": main, "conflicted": conflicted, "intents": intents, "sha": sha}


def launch(conn: sqlite3.Connection, it: sqlite3.Row, stage: str, failure: str, got: Dict[str, Any]) -> str:
    """Under the caller's tx: the fixer run, and what the item keeps of it. Not its state."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    failure = (failure or "").strip()[-FAILURE_CHARS:]
    prompt = fixerbrief.brief(stage=stage, goal=goal["text"], title=it["title"], spec=it["spec"],
                              summary=it["summary"] or "", failure=failure, head=got["main"],
                              landed=got["intents"], conflicted=got["conflicted"])
    rid = store.create_run(conn, it["thread_id"], prompt, row="code", fresh=True,
                           priority="now" if goal["owner"] == "you" else "background")
    note = {"stage": stage, "failure": failure, "run": rid, "at": db.now(), "head": got["main"], "summary": None}
    selfwork._set(conn, it["id"], run_id=rid, fixer=db.dumps(note), base=got["main"], commit_sha=got["sha"],
                  head=None, rebased=None, resolved_on=None, gate2=None, gate2_run=None, gate2_on=None,
                  error=None)
    return rid


def offer(conn: sqlite3.Connection, it: sqlite3.Row, stage: str, failure: str) -> Optional[str]:
    """Under the caller's tx, for the queue's gate 2 and resolve's leave (eki/queue.py,
    eki/resolve.py) until their executor feeds the work model: start the item's one fixer
    run and return the board line, or None — nothing changed — when it gets none."""
    goal = conn.execute("SELECT project FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    if goal is None or goal["project"] is not None or it["fixer"] or not can_start(conn, it):
        return None
    try:
        got = prepare(conn, it)
    except (WorkspaceError, OSError, integration.IntegrationError):
        return None
    rid = launch(conn, it, stage, failure, got)
    selfwork._set(conn, it["id"], state="fixing")
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
    from . import workrun
    said: List[str] = []
    for it in selfwork.items_in(conn, ("fixing",)):
        said += _conclude(conn, it)
    return said + workrun.carry(conn)


def _conclude(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    """A fixer run that ended: its result fed as a build's, or the item left."""
    from . import buildfeed            # buildfeed imports this module: import it late
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if (run is not None and run["state"] in store.ACTIVE) or buildfeed.pending(conn, it["id"]):
        return []
    if run is None or run["state"] != "done":
        why = f"the fixer run ended {run['state'] if run else 'missing'}"
        return _leave(conn, it, why, buildfeed.ended(run, "fixer") if run is not None else None)
    _, _, summary = selfbrief.outcome(store.answer(conn, run["id"]))
    note = {**(of(it) or {}), "summary": summary or "(nothing said)"}
    wt = it["worktree"]
    try:
        if rebase.in_progress(wt):
            left = rebase.markers(wt, note.get("head") or it["base"])
            if left:
                return _leave(conn, it, f"the fixer left conflict markers in {', '.join(left)}", note=note)
            stopped = rebase.proceed(wt)
            if stopped:
                return _leave(conn, it, f"the rebase stopped again on {', '.join(stopped)}", note=note)
        at = git(wt, "rev-parse", "HEAD")
    except (WorkspaceError, OSError) as e:
        return _leave(conn, it, f"the rebase would not go on after the fixer: {e}", note=note)
    with db.tx(conn):                      # its own commits, on main: what is judged if nothing changes
        if not _still(conn, it):
            return []
        selfwork._set(conn, it["id"], fixer=db.dumps(note), **({"commit_sha": at} if at != it["base"] else {}))
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    return buildfeed.built(conn, selfwork.store_item(conn, it["id"]), run, goal, None, fixed=True)


def _still(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    row = conn.execute("SELECT state, run_id FROM items WHERE id=?", (it["id"],)).fetchone()
    return row is not None and row["state"] == "fixing" and row["run_id"] == it["run_id"]


def _leave(conn: sqlite3.Connection, it: sqlite3.Row, why: str, event: Optional[Event] = None,
           note: Optional[Dict[str, Any]] = None) -> List[str]:
    """Stop a rebase the fixer was in; the item is left for a person, `tried` saying how it ended —
    or, when its run stopped on a broken setup, held till that is fixed (eki/itemhold.py)."""
    from . import buildfeed
    wt = it["worktree"]
    try:
        if wt and Path(wt).is_dir():
            rebase.abort(wt)
    except WorkspaceError as e:
        why = f"{why} (and {e})"
    note = note if note is not None else of(it)
    if event is not None and event.data.get("hold") is not None:     # held: it didn't end, nothing tried
        note = None
    if note is not None and note.get("summary") is None:
        note = {**note, "summary": f"({why})"}
    fields = {"fixer": db.dumps(note)} if note is not None else {}
    event = event or Event("item_finished", db.now(), {"leave": why})
    return buildfeed.feed(conn, it, event, then=lambda c: selfwork._set(c, it["id"], **fields) if fields else None,
                          error=why)
