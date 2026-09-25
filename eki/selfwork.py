"""eki builds eki: goals, items, and the runs that carry each item through
plan → build → judge → proposed (docs/self-build.md).

Everything here is a run in an ordinary thread, so a restart loses nothing
and `eki follow` shows any of it. The engine calls `tick` every couple of
seconds; it looks at what finished since and moves each goal or item on
exactly once. This slice proposes: a fit item is a branch `self/<id>` in the
source repo with its checks green, for you to merge (or `eki swap self/<id>`).
"""
from __future__ import annotations

import fnmatch
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import builds, db, paths, selfbrief, store, workspace

log = logging.getLogger("eki.self")

DEFAULTS = {"parallel": 3, "autonomy": "propose"}
#: gate 1, run in the worktree with its own (linked) venv, whatever the engine's environment says
CHECK = '["/bin/sh", "-c", "EKI_PYTHON=$PWD/.venv/bin/python exec bin/check -q"]'


def settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text()).get("self") or {}
    except (OSError, ValueError):
        got = {}
    return {**DEFAULTS, **got}


def source() -> Path:
    return builds.source()


# ---- in ---------------------------------------------------------------------------------

def submit(conn: sqlite3.Connection, text: str, *, plan: bool = True, files: Optional[List[str]] = None,
           owner: str = "you", source_kind: str = "ask") -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to do")
    repo = source()
    base = workspace.head(repo)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, text, source_kind, owner, "planning" if plan else "planned", db.now()))
        if plan:
            wt = workspace.add(repo, f"plan-{gid}", base=base, branch=f"eki/plan-{gid}")
            tid = store.create_thread(conn, f"plan: {text}", str(wt))
            rid = store.create_run(conn, tid, selfbrief.plan(text, base), row="code",
                                   priority="now" if owner == "you" else "background")
            conn.execute("UPDATE goals SET thread_id=?, plan_run=? WHERE id=?", (tid, rid, gid))
        else:
            new_item(conn, gid, text.splitlines()[0][:120], text, files or [], [], False)
    return gid


def new_item(conn: sqlite3.Connection, gid: str, title: str, spec: str, files: List[str],
             deps: List[str], independent: bool) -> str:
    iid = store.new_id()
    now = db.now()
    conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, created_at, "
                 "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (iid, gid, title, spec, db.dumps(files), db.dumps(deps), int(independent), now, now))
    return iid


# ---- the tick -----------------------------------------------------------------------------

def tick(conn: sqlite3.Connection) -> List[str]:
    _tracked.clear()
    said: List[str] = []
    for g in conn.execute("SELECT * FROM goals WHERE state='planning'").fetchall():
        said += _conclude_plan(conn, g)
    for it in items_in(conn, ("building",)):
        said += _conclude_build(conn, it)
    for it in items_in(conn, ("judging",)):
        said += _conclude_judge(conn, it)
    said += _settle_applied(conn)
    said += _start_ready(conn)
    return said


def _settle_applied(conn: sqlite3.Connection) -> List[str]:
    """A proposed item whose commit is now in the source's main — merged by
    you — is applied; items that depend on it may start (from a base that has it)."""
    said = []
    proposed = items_in(conn, ("proposed",))
    if not proposed:
        return said
    main = workspace.head(source())
    for it in proposed:
        if it["commit_sha"] and _is_ancestor(it["commit_sha"], main):
            _set(conn, it["id"], state="applied")
            said.append(f"item {it['id']}: applied — {it['commit_sha'][:12]} is in main ({main[:12]})")
    return said


def _is_ancestor(commit: str, head: str) -> bool:
    """Is `commit` in `head`'s history? Judged by what git prints, not an exit code."""
    if not commit or not head:
        return False
    base = workspace.git(source(), "merge-base", commit, head, check=False)
    return bool(base) and base == workspace.git(source(), "rev-parse", "--verify", f"{commit}^{{commit}}",
                                                 check=False)


def items_in(conn: sqlite3.Connection, states: tuple) -> List[sqlite3.Row]:
    marks = ",".join("?" * len(states))
    return conn.execute(f"SELECT * FROM items WHERE state IN ({marks}) ORDER BY created_at",
                        states).fetchall()


def _set(conn: sqlite3.Connection, iid: str, **fields: Any) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), iid))


def _conclude_plan(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    run = store.run(conn, g["plan_run"]) if g["plan_run"] else None
    if run is None or run["state"] in store.ACTIVE:
        return []
    with db.tx(conn):
        cur = conn.execute("SELECT state FROM goals WHERE id=?", (g["id"],)).fetchone()
        if cur["state"] != "planning":
            return []
        if run["state"] != "done":
            err = f"the plan run ended {run['state']}: {run['error'] or ''}".strip()
            conn.execute("UPDATE goals SET state='failed', error=? WHERE id=?", (err, g["id"]))
            return [f"goal {g['id']}: {err}"]
        try:
            planned = selfbrief.items_in(store.answer(conn, run["id"]))
        except ValueError as e:
            conn.execute("UPDATE goals SET state='failed', error=? WHERE id=?", (str(e), g["id"]))
            return [f"goal {g['id']}: {e}"]
        ids: Dict[str, str] = {}
        for it in planned:
            ids[it["title"]] = new_item(conn, g["id"], it["title"], it["spec"], it["files"], [],
                                        it["independent"])
        for it in planned:
            deps = [ids[d] for d in it["deps"] if d in ids and ids[d] != ids[it["title"]]]
            _set(conn, ids[it["title"]], deps=db.dumps(deps))
        conn.execute("UPDATE goals SET state='planned' WHERE id=?", (g["id"],))
    workspace.remove(source(), f"plan-{g['id']}", delete_branch=True)
    return [f"goal {g['id']}: {len(planned)} item(s) planned"]


def _conclude_build(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE:
        return []
    goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    with db.tx(conn):
        if conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()["state"] != "building":
            return []
        if run["state"] != "done":
            _set(conn, it["id"], state="left", error=f"the build run ended {run['state']}: {run['error'] or ''}")
            return [f"item {it['id']}: left — the build run ended {run['state']}"]
        verdict, reason, summary = selfbrief.outcome(store.answer(conn, run["id"]))
        sha = workspace.commit_all(it["worktree"], f"self: {it['title']}\n\n{goal['text']}"[:2000])
        sha = sha or it["commit_sha"]           # a retry that left things as they were: judge again
        touched = workspace.changed(it["worktree"], it["base"]) if sha else []
        if verdict == "person" or sha is None:
            why = reason or ("the agent changed nothing" if sha is None else "")
            _set(conn, it["id"], state="left", error=why, summary=summary, commit_sha=sha,
                 touched=db.dumps(touched))
            return [f"item {it['id']}: left for you — {why}"]
        rid = store.create_run(conn, it["thread_id"], CHECK, provider="command", priority=run["priority"])
        _set(conn, it["id"], state="judging", run_id=rid, commit_sha=sha, summary=summary,
             touched=db.dumps(touched), error=None if verdict == "done" else f"partial: {reason}")
    return [f"item {it['id']}: built ({len(touched)} files); judging"]


def _conclude_judge(conn: sqlite3.Connection, it: sqlite3.Row) -> List[str]:
    run = store.run(conn, it["run_id"])
    if run is None or run["state"] in store.ACTIVE:
        return []
    tail = "\n".join(store.answer(conn, run["id"]).strip().splitlines()[-25:])
    if run["state"] != "done":
        tail = (tail + f"\n(checks {run['state']}: {run['error'] or ''})").strip()
    with db.tx(conn):
        if conn.execute("SELECT state FROM items WHERE id=?", (it["id"],)).fetchone()["state"] != "judging":
            return []
        if run["state"] == "done":
            _set(conn, it["id"], state="proposed", verdict=tail[-800:])
            return [f"item {it['id']}: fit — proposed on {it['branch']}"]
        if it["tries"] < 2 and run["state"] == "failed":
            _set(conn, it["id"], state="waiting", verdict=tail[-800:], error="checks failed; trying once more")
            return [f"item {it['id']}: checks failed; one more try"]
        _set(conn, it["id"], state="unfit", verdict=tail[-800:], error=f"checks {run['state']}")
    return [f"item {it['id']}: unfit — left for you"]


# ---- starting items -------------------------------------------------------------------------

def _start_ready(conn: sqlite3.Connection) -> List[str]:
    parallel = int(settings()["parallel"])
    live = items_in(conn, ("building", "judging"))
    if len(live) >= parallel:
        return []
    # a dependency counts once it is in main: the item then starts from a base that has it
    done_ids = {r["id"] for r in items_in(conn, ("applied",))}
    taken: List[Set[str]] = [claims(x) for x in live]
    names = None
    said = []
    for it in items_in(conn, ("waiting",)):
        if len(live) >= parallel:
            break
        deps = json.loads(it["deps"] or "[]")
        if any(d not in done_ids for d in deps):
            continue
        if names is None:
            names = tracked(source())
        mine = expand(json.loads(it["files"] or "[]"), names)
        if not it["independent"] and any(mine & t for t in taken):
            continue
        goal = conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
        others = [(x["title"], json.loads(x["files"] or "[]")) for x in live]
        with db.tx(conn):
            _start_build(conn, it, goal, others)
        live.append(store_item(conn, it["id"]))
        taken.append(mine)
        said.append(f"item {it['id']}: building ({it['title'][:50]})")
    return said


def claims(it: sqlite3.Row) -> Set[str]:
    files = set(json.loads(it["files"] or "[]")) | set(json.loads(it["touched"] or "[]"))
    return expand(sorted(files), tracked(source()))


def _start_build(conn: sqlite3.Connection, it: sqlite3.Row, goal: sqlite3.Row, others) -> None:
    repo = source()
    if not it["worktree"]:
        base = workspace.head(repo)
        wt = workspace.add(repo, it["id"], base=base, branch=f"self/{it['id']}")
        tid = store.create_thread(conn, f"self: {it['title']}", str(wt))
        _set(conn, it["id"], worktree=str(wt), branch=f"self/{it['id']}", base=base, thread_id=tid)
        it = store_item(conn, it["id"])
    failure = it["verdict"] if it["tries"] else None
    prompt = selfbrief.build(goal=goal["text"], title=it["title"], spec=it["spec"],
                             files=json.loads(it["files"] or "[]"), branch=it["branch"], base=it["base"],
                             source=str(repo), others=others, failure=failure)
    rid = store.create_run(conn, it["thread_id"], prompt, row="code",
                           priority="now" if goal["owner"] == "you" else "background")
    _set(conn, it["id"], state="building", run_id=rid, tries=it["tries"] + 1, error=None)


def store_item(conn: sqlite3.Connection, iid: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM items WHERE id=? OR id LIKE ?", (iid, iid + "%")).fetchone()


# ---- write-sets ------------------------------------------------------------------------------

_tracked: Dict[str, List[str]] = {}


def tracked(repo: Path) -> List[str]:
    key = str(repo)
    if key not in _tracked:
        _tracked[key] = workspace.git(repo, "ls-files").splitlines()
    return _tracked[key]


def expand(patterns: List[str], names: List[str]) -> Set[str]:
    """The files a write-set means. No write-set means everything."""
    if not patterns:
        return set(names) | {"*"}
    out: Set[str] = set()
    for p in patterns:
        p = p.strip().lstrip("./")
        if p.endswith("/"):
            p += "*"
        hits = fnmatch.filter(names, p)
        out |= set(hits) if hits else {p}          # a file that doesn't exist yet: by name
    return out


# ---- out ---------------------------------------------------------------------------------------

def drop(conn: sqlite3.Connection, iid: str) -> str:
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    if it["run_id"]:
        store.cancel(conn, it["run_id"])
    _set(conn, it["id"], state="dropped")
    if it["worktree"]:
        workspace.remove(source(), it["id"], delete_branch=(it["commit_sha"] is None))
    return it["id"]


def retry(conn: sqlite3.Connection, iid: str) -> str:
    it = store_item(conn, iid)
    if it is None:
        raise KeyError(f"no item {iid}")
    if it["state"] in ("building", "judging"):
        raise ValueError(f"item {it['id']} is {it['state']}")
    fields: Dict[str, Any] = {"state": "waiting", "error": None}
    if it["worktree"] and not Path(it["worktree"]).exists():     # dropped: start afresh
        fields.update(worktree=None, branch=None, base=None, commit_sha=None, tries=0,
                      touched="[]", verdict=None, summary=None)
    _set(conn, it["id"], **fields)
    return it["id"]
