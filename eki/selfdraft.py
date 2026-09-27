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
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db, integration, paths, projectbrief, projects, selfbrief, store, workspace
from .routing import table

#: the old eki, read as reference by a draft when it is there
OLD = "~/eki-2026-09-26"

#: the first line of a ranking goal's wish; one `<n>. roadmap:<key> <title>` line per entry follows
RANK = "rank the open ROADMAP.md entries"

TICK = """\

When the plan is made: add a last, docs-only item that ticks this entry's box
in ROADMAP.md (change `- [ ]` to `- [x]` on the entry "{title}"), and make that
item depend on every other item."""


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
    """Start the plan run for goal `gid` (inside the caller's tx), in a
    read-only worktree of the goal's repo: eki's own, or its project's."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    repo = projects.repo_for(conn, goal) if goal is not None else integration.repo()
    wt = workspace.add(repo, f"plan-{gid}", base=base, branch=f"eki/plan-{gid}")
    tid = store.create_thread(conn, f"plan: {text}", str(wt))
    provider, model = planner()
    project = projects.get(conn, goal["project"]) if goal is not None and goal["project"] else None
    brief = (selfbrief.plan(text, base) if project is None
             else projectbrief.plan(text, base, project, standing=bool(goal["standing_id"])))
    rid = store.create_run(conn, tid, brief, provider=provider, model=model,
                           row="code", priority=_priority(owner))
    conn.execute("UPDATE goals SET thread_id=?, plan_run=?, state='planning' WHERE id=?", (tid, rid, gid))
    return rid


def open_rank(conn: sqlite3.Connection, entries: List[Any], why: str) -> str:
    """Open a goal whose draft run ranks the open ROADMAP entries (roadmap.Entry)
    and drafts the winner's goal. The wish lists the entries by number, so
    `conclude` maps PICK back to a key from the goals table alone."""
    from . import digest, score        # both read selfwork's settings: import them late
    base = integration.sync()
    wish = "\n".join([RANK] + [f"{n}. roadmap:{e.key} {e.title}" for n, e in enumerate(entries, 1)])
    now = time.time()
    last = digest.latest()
    prompt = selfbrief.rank(entries, base, digest=str(last) if last else None,
                            score=score.window(conn, now - 7 * 86400, now))
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, wish, source, owner, state, created_at, why, pick_key) "
                     "VALUES (?,?,?,'roadmap','eki','drafting',?,?,NULL)", (gid, wish, wish, db.now(), why))
        wt = workspace.add(integration.repo(), f"draft-{gid}", base=base, branch=f"eki/draft-{gid}")
        tid = store.create_thread(conn, f"draft: {RANK}", str(wt))
        provider, model = planner()
        rid = store.create_run(conn, tid, prompt, provider=provider, model=model, row="code",
                               priority=_priority("eki"))
        conn.execute("UPDATE goals SET thread_id=?, draft_run=? WHERE id=?", (tid, rid, gid))
    return gid


def _ranked(wish: str) -> Dict[int, Tuple[str, str]]:
    """{n: (pick_key, title)} from a ranking goal's wish."""
    out: Dict[int, Tuple[str, str]] = {}
    for ln in (wish or "").splitlines():
        m = re.match(r"^(\d+)\. (roadmap:\S+) ?(.*)$", ln.strip())
        if m:
            out[int(m.group(1))] = (m.group(2), m.group(3).strip())
    return out


def _conclude_rank(conn: sqlite3.Connection, g: sqlite3.Row, answer: str) -> List[str]:
    """A ranking draft's answer (inside the caller's tx): plan the picked entry, or leave it."""
    gid = g["id"]
    n, why = selfbrief.pick_in(answer)
    picked = _ranked(g["wish"]).get(n) if n is not None else None
    if picked is None:
        err = "the ranking has no PICK" if n is None else f"the ranking picked {n}, which isn't listed"
        conn.execute("UPDATE goals SET state='left', error=?, pick_key='roadmap:none' WHERE id=?", (err, gid))
        return [f"goal {gid}: left — {err}"]
    key, title = picked
    why = why or g["why"]
    goal, err = selfbrief.goal_in(answer)
    if goal is None:
        conn.execute("UPDATE goals SET state='left', error=?, pick_key=?, why=? WHERE id=?",
                     (err, key, why, gid))
        return [f"goal {gid}: left — {err}"]
    text = goal + TICK.format(title=title)
    conn.execute("UPDATE goals SET text=?, drafted_at=?, pick_key=?, why=? WHERE id=?",
                 (text, db.now(), key, why, gid))
    start_plan(conn, gid, text, integration.sync(), g["owner"])
    return [f"goal {gid}: picked {key} → planning"]


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
        elif g["source"] == "roadmap":
            said = _conclude_rank(conn, g, store.answer(conn, run["id"]))
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


def retry(conn: sqlite3.Connection, gid: str) -> str:
    """Run a failed or left goal's plan again — or its draft, when the draft
    never produced a goal. Returns the goal's new state."""
    g = conn.execute("SELECT * FROM goals WHERE id=? OR id LIKE ? ORDER BY id=? DESC",
                     (gid, gid + "%", gid)).fetchone()
    if g is None:
        raise KeyError(f"no goal {gid}")
    if g["state"] not in ("failed", "left"):
        raise ValueError(f"goal {g['id']} is {g['state']}")
    project = projects.get(conn, g["project"]) if g["project"] else None
    base = integration.sync() if project is None else projects.base(project)
    plan = bool(g["plan_run"] or g["drafted_at"] or not g["draft_run"])
    with db.tx(conn):
        cur = conn.execute("SELECT state FROM goals WHERE id=?", (g["id"],)).fetchone()
        if cur["state"] not in ("failed", "left"):
            raise ValueError(f"goal {g['id']} is {cur['state']}")
        conn.execute("UPDATE goals SET error=NULL WHERE id=?", (g["id"],))
        if plan:
            start_plan(conn, g["id"], g["text"], base, g["owner"])
        else:
            start_draft(conn, g["id"], g["wish"] or g["text"], base, g["owner"])
    return "planning" if plan else "drafting"
