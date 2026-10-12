"""The per-item judge: an independent run reads one built item against its checks
(docs/self-build.md, "Judging"; eki/verdicts.py for the verdict's shape).

When a build ends, review.ask calls `start_item` in the same transaction as
gate 1: a judge run (eki/judgerun.py) on the judge role's pick, a fresh run of its own,
in the read-only worktree `judge-<item id>` at the item's commit, registered
in `judgings` with scope `item` for `spec` (does the change do what the
item's spec says) and the goal checks the item carries (items.checks). It
files one verdict per check with `eki verdict`; its answer text is never read.

`read` turns the run's verdicts (and a playtester's, when gate 1 was a `play`
run) into the item's word: all passed → ok; any failed → no, with each failed
check's suggestion; uncertain → one `second` run on yet another backend
(`second`), then a person. A missing verdict or a failed judge run is
`none: <why>`: the judge never holds an item it couldn't judge.

Item-level verdicts are round 0: they come before the goal's acceptance
rounds, so a final verdict always stands over them (verdicts.latest).
Nothing is kept in memory: everything is read from items, runs, judgings and
verdicts, and every write is in the caller's transaction.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import db, integration, judgebrief, judgerun, projects, store, verdicts, workspace
from .components import knowledge

#: item-level verdicts come before the goal's acceptance rounds
ROUND = 0
SPEC_TEXT = "the change does what the item's spec says, without obvious bugs, missing tests or stray changes"


def _goal(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM goals WHERE id=?", (it["goal_id"],)).fetchone()


def item_checks(conn: sqlite3.Connection, it: sqlite3.Row) -> List[Tuple[str, str]]:
    """The goal checks the item carries, [(id, text)] in the goal's order."""
    try:
        ids = json.loads(it["checks"] or "[]") if "checks" in it.keys() else []
    except ValueError:
        ids = []
    want = {str(c).lower() for c in ids} if isinstance(ids, list) else set()
    return [(c["id"], c["text"]) for c in verdicts.checks_of(conn, it["goal_id"]) if c["id"] in want]


def builders(conn: sqlite3.Connection, it: sqlite3.Row) -> Set[str]:
    """The providers that built the item: every agent run in its thread (builds, retries, fixes)."""
    if not it["thread_id"]:
        return set()
    return {r["provider"] for r in store.thread_runs(conn, it["thread_id"])
            if r["provider"] and r["provider"] != "command" and verdicts.judging(conn, r["id"]) is None}


def _repo(conn: sqlite3.Connection, goal: Optional[sqlite3.Row]) -> str:
    return str(projects.repo_for(conn, goal)) if goal is not None else str(integration.repo())


def _tree(conn: sqlite3.Connection, it: sqlite3.Row, repo: str) -> str:
    """`judge-<item id>`, moved to the item's commit when an earlier build's judge left it there."""
    key = f"judge-{it['id']}"
    dest = workspace.path_for(key)
    if (dest / ".git").exists() and it["commit_sha"]:
        workspace.git(dest, "reset", "-q", "--hard", it["commit_sha"], check=False)
        workspace.git(dest, "clean", "-qfd", check=False)
    return key


def prompt(conn: sqlite3.Connection, it: sqlite3.Row, scope: str, checks: List[Tuple[str, str]]) -> str:
    """The judge's brief (judgebrief.brief): the item's spec carries what its builder was told to read."""
    goal = _goal(conn, it)
    base, sha = it["base"] or "", it["commit_sha"] or ""
    spec = (it["spec"] or "").strip() or "(no spec)"
    told = _told(conn, it, goal)
    if told:
        spec += f"\n\nWhat the builder was told to read:\n\n{told}"
    diff = (f"git diff {base[:12]}..{sha[:12]}" if base else f"git show {sha[:12]}") if sha else None
    return judgebrief.brief((goal["text"] if goal is not None else "") or "", checks, scope,
                            item={"title": it["title"], "spec": spec}, diff=diff, claim=it["summary"] or None)


def _told(conn: sqlite3.Connection, it: sqlite3.Row, goal: Optional[sqlite3.Row]) -> str:
    """The knowledge brief the item's builder got ('' for eki's own items, which have no cards)."""
    try:
        where = it["worktree"] if it["worktree"] and Path(it["worktree"]).exists() else None
        if where is None and goal is not None and goal["project"]:
            p = projects.get(conn, goal["project"])
            where = p["path"] if p is not None else None
        return knowledge.brief(where, conn, context=knowledge.names(it["context"])) if where else ""
    except Exception:  # noqa: BLE001 — the brief never keeps an item from its judge
        return ""


def _start(conn: sqlite3.Connection, it: sqlite3.Row, scope: str, checks: List[Tuple[str, str]],
           avoid: Set[str]) -> Tuple[Optional[str], str]:
    if not it["commit_sha"]:
        return None, "the item has no commit to judge"
    goal = _goal(conn, it)
    repo = _repo(conn, goal)
    background = goal is not None and goal["owner"] != "you"
    return judgerun.start(conn, goal_id=it["goal_id"], item_id=it["id"], scope=scope,
                          checks=[cid for cid, _ in checks], round=ROUND, avoid=avoid,
                          tree=_tree(conn, it, repo), repo=repo, ref=it["commit_sha"],
                          prompt=prompt(conn, it, scope, checks), background=background)


def start_item(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """The item's judge run: (run id, '') or (None, why). Called under db.tx; never moves the item."""
    checks = [(verdicts.SPEC, SPEC_TEXT)] + item_checks(conn, it)
    return _start(conn, it, "item", checks, builders(conn, it))


def is_second(conn: sqlite3.Connection, it: sqlite3.Row) -> bool:
    """Is the item's judge run already the second one?"""
    j = verdicts.judging(conn, it["review_run"]) if it["review_run"] else None
    return j is not None and j["scope"] == "second"


def second(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[str]:
    """One more judge run for only the uncertain checks, off the builder's and the first judge's
    backends; items.review_run moves to it and the item stays judging. None when none can start."""
    first = store.run(conn, it["review_run"]) if it["review_run"] else None
    if first is None or is_second(conn, it):
        return None
    got = _verdicts(conn, it)
    unsure = [cid for cid, v in got.items() if v["verdict"] == "uncertain"]
    if not unsure:
        return None
    text = dict([(verdicts.SPEC, SPEC_TEXT)] + item_checks(conn, it))
    avoid = builders(conn, it) | {first["provider"]} | {v["provider"] for v in got.values()
                                                         if v["provider"] and v["provider"] != "command"}
    rid, _ = _start(conn, it, "second", [(cid, text.get(cid, cid)) for cid in unsure], avoid)
    if rid is not None:
        conn.execute("UPDATE items SET review_run=?, updated_at=? WHERE id=?", (rid, db.now(), it["id"]))
    return rid


def played(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[sqlite3.Row]:
    """The item's gate 1 run when it is a registered `play` run (workflowgates.play)."""
    j = verdicts.judging(conn, it["run_id"]) if it["run_id"] else None
    return j if j is not None and j["scope"] == "play" else None


def _verdicts(conn: sqlite3.Connection, it: sqlite3.Row) -> Dict[str, sqlite3.Row]:
    """{check id: the worst verdict} of the judge run and the play run."""
    rows: List[sqlite3.Row] = []
    for rid in (it["review_run"], it["run_id"] if played(conn, it) else None):
        if rid:
            rows += list(verdicts.outcome(conn, rid).values())
    by: Dict[str, List[sqlite3.Row]] = {}
    for r in rows:
        by.setdefault(r["check_id"], []).append(r)
    return {cid: verdicts.worst(rs) for cid, rs in by.items()}


def _one(text: str) -> str:
    return " ".join((text or "").split())


def read(conn: sqlite3.Connection, it: sqlite3.Row) -> Tuple[Optional[str], str]:
    """The item's word once its judge run ended: ('ok', ''), ('no', '<id>: <suggestion>' a line per
    failed check), ('uncertain', '<id>: <limitation>' joined by ' · ') or (None, why)."""
    got = _verdicts(conn, it)
    failed = [(cid, v) for cid, v in got.items() if v["verdict"] == "failed"]
    if failed:
        return "no", "\n".join(f"{cid}: {_one(v['suggestion'])}" for cid, v in failed)
    run = store.run(conn, it["review_run"]) if it["review_run"] else None
    j = verdicts.judging(conn, it["review_run"]) if it["review_run"] else None
    if run is not None and j is not None:
        if run["state"] != "done":
            return None, f"the judge run ended {run['state']}: {run['error'] or ''}".strip(" :")
        missing = [cid for cid in verdicts.listed(j) if cid not in got]
        if missing:
            return None, f"the judge filed no verdict for {', '.join(missing)}"
    elif it["review_run"]:
        return None, "the judge run is gone"
    unsure = [(cid, v) for cid, v in got.items() if v["verdict"] == "uncertain"]
    if unsure:
        return "uncertain", " · ".join(f"{cid}: {_one(v['limitation'])}" for cid, v in unsure)
    if not got:
        return None, "no verdict was filed"
    return "ok", ""


def play(conn: sqlite3.Connection, it: sqlite3.Row, wf) -> Optional[str]:
    """Gate 1 of a playtester card, just made by selfwork (queued, in this transaction): give the
    playtester the item's checks, pipe its answer into `eki verdict --lines`, and register the run
    with scope `play`. The run's id, or None when there is nothing to give it."""
    from . import workflow, workflowgates
    checks = item_checks(conn, it)
    run = store.run(conn, it["run_id"]) if it["run_id"] else None
    if (wf is None or not workflow.has(wf, "playtester") or not checks or run is None
            or run["provider"] != "command" or run["state"] != "queued" or verdicts.judging(conn, run["id"])):
        return None
    try:
        touched = json.loads(it["touched"] or "[]")
    except ValueError:
        touched = []
    got = workflowgates.gate(wf, it["title"], touched, checks)
    if got is None:
        return None
    store.update_run(conn, run["id"], prompt=got[0])
    conn.execute("UPDATE items SET judged_by=? WHERE id=?", (got[1], it["id"]))
    verdicts.register(conn, run["id"], it["goal_id"], it["id"], "play", [c for c, _ in checks], ROUND, ())
    return run["id"]
