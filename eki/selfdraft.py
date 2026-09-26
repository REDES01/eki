"""The thinking runs in front of the builds: draft (a wish becomes a goal) and
plan (a goal becomes items) — docs/self-build.md, "The draft".

Both run in a read-only worktree of the integration repo, pinned to the
planner (routing.json `self.planner`: the strongest model eki has). The
goals table holds everything: a restart mid-draft just waits for the run,
and `conclude` rechecks the goal's state under a transaction, so it moves a
goal on exactly once.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import db, integration, paths, selfbrief, store, workspace
from .routing import table

#: the old eki, read as reference by a draft when it is there
OLD = "~/eki-2026-09-26"


def planner() -> Tuple[str, Optional[str]]:
    """(provider, model) for draft and plan runs. The provider defaults to the
    `code` row's first choice; no model means the provider's default."""
    try:
        got = json.loads(paths.config("routing").read_text()).get("self") or {}
    except (OSError, ValueError):
        got = {}
    want = got.get("planner") if isinstance(got.get("planner"), dict) else {}
    provider = str(want.get("provider") or "").strip() or table.row("code")["targets"][0]
    model = str(want.get("model") or "").strip() or None
    return provider, model


def _priority(owner: str) -> str:
    return "now" if owner == "you" else "background"


def start_draft(conn: sqlite3.Connection, gid: str, wish: str, base: str, owner: str) -> str:
    """Start the draft run for goal `gid` (inside the caller's tx)."""
    from . import digest               # digest reads selfwork's settings: import it late
    wt = workspace.add(integration.repo(), f"draft-{gid}", base=base, branch=f"eki/draft-{gid}")
    tid = store.create_thread(conn, f"draft: {wish}", str(wt))
    last = digest.latest()
    old = Path(OLD).expanduser()
    prompt = selfbrief.draft(wish, base, digest=str(last) if last else None,
                             old=str(old) if old.exists() else None)
    provider, model = planner()
    rid = store.create_run(conn, tid, prompt, provider=provider, model=model, row="code",
                           priority=_priority(owner))
    conn.execute("UPDATE goals SET thread_id=?, draft_run=?, state='drafting' WHERE id=?", (tid, rid, gid))
    return rid


def start_plan(conn: sqlite3.Connection, gid: str, text: str, base: str, owner: str) -> str:
    """Start the plan run for goal `gid` (inside the caller's tx)."""
    wt = workspace.add(integration.repo(), f"plan-{gid}", base=base, branch=f"eki/plan-{gid}")
    tid = store.create_thread(conn, f"plan: {text}", str(wt))
    provider, model = planner()
    rid = store.create_run(conn, tid, selfbrief.plan(text, base), provider=provider, model=model,
                           row="code", priority=_priority(owner))
    conn.execute("UPDATE goals SET thread_id=?, plan_run=?, state='planning' WHERE id=?", (tid, rid, gid))
    return rid


def conclude(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    """Move a drafting goal on once its draft run has ended: to planning, left or failed."""
    run = store.run(conn, g["draft_run"]) if g["draft_run"] else None
    if run is None or run["state"] in store.ACTIVE:
        return []
    gid = g["id"]
    with db.tx(conn):
        cur = conn.execute("SELECT state FROM goals WHERE id=?", (gid,)).fetchone()
        if cur is None or cur["state"] != "drafting":
            return []
        if run["state"] != "done":
            err = f"the draft run ended {run['state']}: {run['error'] or ''}".strip()
            conn.execute("UPDATE goals SET state='failed', error=? WHERE id=?", (err, gid))
            said = [f"goal {gid}: {err}"]
        else:
            goal, why = selfbrief.goal_in(store.answer(conn, run["id"]))
            if goal is None:
                conn.execute("UPDATE goals SET state='left', error=? WHERE id=?", (why, gid))
                said = [f"goal {gid}: left — {why}"]
            else:
                conn.execute("UPDATE goals SET text=?, drafted_at=? WHERE id=?", (goal, db.now(), gid))
                start_plan(conn, gid, goal, integration.sync(), g["owner"])
                said = [f"goal {gid}: drafted → planning"]
    workspace.remove(integration.repo(), f"draft-{gid}", delete_branch=True)
    return said
