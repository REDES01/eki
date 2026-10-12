"""The thinking runs in front of the builds: draft (a wish becomes a goal) and
plan (a goal becomes items) — docs/self-build.md, "The draft".

Both run in a read-only worktree of the integration repo, pinned to the
planner (the `planner` role, eki/roles.py: the strongest model eki has). The
goals table holds everything: a restart mid-draft just waits for the run,
and `conclude` feeds plan_ready to the work model (eki/workstep.py), which
rechecks the goal's state under its transaction, so a goal moves on exactly
once.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import (db, drybuild, integration, projectbrief, projects, roles, selfbrief, store, targets,
               verdicts, workspace)
from .workmodel import Event

#: the old eki, read as reference by a draft when it is there
OLD = "~/eki-2026-09-26"

#: the first line of a ranking goal's wish; one `<n>. roadmap:<key> <title>` line per entry follows
RANK = "rank the open ROADMAP.md entries"

TICK = """\

When the plan is made: add a last, docs-only item that ticks this entry's box
in ROADMAP.md (change `- [ ]` to `- [x]` on the entry "{title}"), and make that
item depend on every other item."""


def planner() -> Tuple[str, Optional[str], Optional[str]]:
    """(provider, model, effort) for draft and plan runs: the `planner` role
    (eki/roles.py; by default `self.planner`, else the `code` row's first
    choice). No model or effort means the program's own default. When neither the role
    nor its default can run, the default's provider still comes back, so the
    run waits for it with its reason rather than go nowhere."""
    pick = roles.pick("planner")
    if pick.provider is not None:
        return pick.provider, pick.model, pick.effort
    return targets.provider_of(roles.default("planner")), None, None


def _priority(owner: str) -> str:
    return "now" if owner == "you" else "background"


def start_draft(conn: sqlite3.Connection, gid: str, wish: str, base: str, owner: str) -> str:
    """Start the draft run for goal `gid` (inside the caller's tx), in a
    read-only worktree of the goal's repo: eki's own, or its project's."""
    from . import digest               # digest reads selfwork's settings: import it late
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    repo = projects.repo_for(conn, goal) if goal is not None else integration.repo()
    wt = workspace.add(repo, f"draft-{gid}", base=base, branch=f"eki/draft-{gid}")
    tid = store.create_thread(conn, f"draft: {wish}", str(wt), by="eki")
    project = projects.get(conn, goal["project"]) if goal is not None and goal["project"] else None
    if project is not None:
        prompt = projectbrief.draft(wish, base, project)
    else:
        last = digest.latest()
        old = Path(OLD).expanduser()
        prompt = selfbrief.draft(wish, base, digest=str(last) if last else None,
                                 old=str(old) if old.exists() else None)
    provider, model, effort = planner()
    rid = store.create_run(conn, tid, prompt, provider=provider, model=model, effort=effort,
                           row="code", priority=_priority(owner))
    conn.execute("UPDATE goals SET thread_id=?, draft_run=?, state='drafting' WHERE id=?", (tid, rid, gid))
    return rid


def start_plan(conn: sqlite3.Connection, gid: str, text: str, base: str, owner: str) -> str:
    """Start the plan run for goal `gid` (inside the caller's tx), in a
    read-only worktree of the goal's repo: eki's own, or its project's."""
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    repo = projects.repo_for(conn, goal) if goal is not None else integration.repo()
    wt = workspace.add(repo, f"plan-{gid}", base=base, branch=f"eki/plan-{gid}")
    tid = store.create_thread(conn, f"plan: {text}", str(wt), by="eki")
    provider, model, effort = planner()
    project = projects.get(conn, goal["project"]) if goal is not None and goal["project"] else None
    checks = [(c["id"], c["text"]) for c in verdicts.checks_of(conn, gid)]
    brief = (selfbrief.plan(text, base, checks) if project is None
             else projectbrief.plan(text, base, project, standing=bool(goal["standing_id"]), checks=checks))
    rid = store.create_run(conn, tid, brief, provider=provider, model=model, effort=effort,
                           row="code", priority=_priority(owner))
    conn.execute("UPDATE goals SET thread_id=?, plan_run=?, state='planning' WHERE id=?", (tid, rid, gid))
    drybuild.start(conn, gid, project, base, text, _priority(owner))
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
        tid = store.create_thread(conn, f"draft: {RANK}", str(wt), by="eki")
        provider, model, effort = planner()
        rid = store.create_run(conn, tid, prompt, provider=provider, model=model, effort=effort,
                               row="code", priority=_priority("eki"))
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


def _rank(conn: sqlite3.Connection, g: sqlite3.Row, answer: str) -> Tuple[Dict[str, object], Dict[str, object]]:
    """A ranking draft's answer: (what plan_ready says, the goal's own columns to write)."""
    n, why = selfbrief.pick_in(answer)
    picked = _ranked(g["wish"]).get(n) if n is not None else None
    if picked is None:
        err = "the ranking has no PICK" if n is None else f"the ranking picked {n}, which isn't listed"
        return {"left": err}, {"pick_key": "roadmap:none", "error": err}
    key, title = picked
    why = why or g["why"]
    goal, err = selfbrief.goal_in(answer)
    if goal is None:
        return {"left": err}, {"pick_key": key, "why": why, "error": err}
    return {}, {"text": goal + TICK.format(title=title), "drafted_at": db.now(), "pick_key": key, "why": why}


def conclude(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    """A drafting goal whose draft run ended: plan_ready, and the model moves it to planning, left or failed."""
    from . import buildfeed            # buildfeed reads half of self-work: import it late
    run = store.run(conn, g["draft_run"]) if g["draft_run"] else None
    if run is None or run["state"] in store.ACTIVE:
        return []
    gid = g["id"]
    d: Dict[str, object] = {"of": "draft", "ended": run["state"], "error": run["error"] or ""}
    cols: Dict[str, object] = {}
    if run["state"] == "done":
        d["error"] = None
        if g["source"] == "roadmap":
            more, cols = _rank(conn, g, store.answer(conn, run["id"]))
            d.update(more)
        else:
            goal, why = selfbrief.goal_in(store.answer(conn, run["id"]))
            if goal is None:
                d["left"], cols = why, {"error": why}
            else:
                cols = {"text": goal, "drafted_at": db.now()}

    def write(c: sqlite3.Connection) -> None:
        if cols:
            c.execute(f"UPDATE goals SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), gid))
        if cols.get("text"):            # the goal's checks, stored before its plan run reads them
            verdicts.store_checks(c, gid, verdicts.checks_in(str(cols["text"])))
    try:
        said = buildfeed.feed_goal(conn, g, Event("plan_ready", db.now(), d), then=write)
    except KeyError:                   # its project is gone: nothing of it to plan or clean
        with db.tx(conn):
            err = f"its project {g['project']} is gone"
            if conn.execute("UPDATE goals SET state='failed', error=? WHERE id=? AND state='drafting'",
                            (err, gid)).rowcount:
                return [f"goal {gid}: {err}"]
        return []
    if g["source"] == "roadmap" and said and "planning" in said[0]:
        said = [f"goal {gid}: picked {cols['pick_key']} → planning"]
    workspace.remove(projects.repo_for(conn, g), f"draft-{gid}", delete_branch=True)
    return said


def _base(conn: sqlite3.Connection, g: sqlite3.Row) -> str:
    """Where a goal's plan starts: eki's integration tip, or its project's branch tip."""
    project = projects.get(conn, g["project"]) if g["project"] else None
    return integration.sync() if project is None else projects.base(project)


def retry(conn: sqlite3.Connection, gid: str) -> str:
    """Run a failed or left goal's plan again — or its draft, when the draft
    never produced a goal. Returns the goal's new state."""
    from . import buildfeed, workrun, workstep
    g = conn.execute("SELECT * FROM goals WHERE id=? OR id LIKE ? ORDER BY id=? DESC",
                     (gid, gid + "%", gid)).fetchone()
    if g is None:
        raise KeyError(f"no goal {gid}")
    plan = bool(g["plan_run"] or g["drafted_at"] or not g["draft_run"])
    try:
        buildfeed.feed_goal(conn, g, Event("person_decided", db.now(), {"decision": "retry", "plan": plan}),
                            then=lambda c: c.execute("UPDATE goals SET error=NULL WHERE id=?", (g["id"],)))
    except workstep.Refused:
        raise ValueError(f"goal {g['id']} is {g['state']}") from None
    workrun.carry(conn)                 # the run starts now, not at the next tick
    return conn.execute("SELECT state FROM goals WHERE id=?", (g["id"],)).fetchone()["state"]
