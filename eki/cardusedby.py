"""A card's "Used by", read from the runs table and the workflow card: nothing new is recorded.

stages: the workflow stage titles the card feeds (api_workflow.feeds). runs: the newest runs that used
it — for a card called with `eki use`, the caller of each call (else the call itself), plus for a serve
card the runs its preview links were started or used by; for a passive card (it goes to every agent),
the project's own recent runs. line: one plain sentence on how much and by whom.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import api_workflow, components, projectchats, projects
from .components import servers, uses

#: kinds a run is called by with `eki use`; the others reach every agent of the project
CALLED = ("service", "assets", "agent", "serve")
NEWEST = 5
#: what a run of each sort is called, in plural, in the line
_PLURAL = {"build": "build agents", "plan": "planners", "review": "reviewers", "chore": "chores",
           "chat": "chats", "you": "you", "check": "checks"}


def short(text: str, n: int = 40) -> str:
    """The first line of text, cut at about n characters with '…'."""
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= n else line[:n - 1].rstrip() + "…"


def ago(t: float, now: Optional[float] = None) -> str:
    s = max(0, (now or time.time()) - t)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    d = int(s // 86400)
    return f"{d} day{'s' if d > 1 else ''} ago"


def _one(conn: sqlite3.Connection, sql: str, args: tuple) -> Optional[sqlite3.Row]:
    try:
        return conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


def _run(conn: sqlite3.Connection, rid: str) -> Optional[sqlite3.Row]:
    return _one(conn, "SELECT * FROM runs WHERE id=?", (rid,))


def sort_of(conn: sqlite3.Connection, r: sqlite3.Row) -> str:
    """build, plan, review, chore, check or chat: what a run was for."""
    rid = r["id"]
    if _one(conn, "SELECT 1 FROM items WHERE review_run=?", (rid,)):
        return "review"
    if _one(conn, "SELECT 1 FROM items WHERE run_id=? OR thread_id=?", (rid, r["thread_id"])):
        return "build"
    if _one(conn, "SELECT 1 FROM goals WHERE plan_run=? OR draft_run=?", (rid, rid)):
        return "plan"
    if r["provider"] == "command":
        return "check"
    return "chore" if r["row"] == "chore" else "chat"


def title(conn: sqlite3.Connection, r: sqlite3.Row) -> str:
    """A run in words: its item's title, its goal's text, its chat's title, else its prompt."""
    it = _one(conn, "SELECT title FROM items WHERE run_id=? OR thread_id=? ORDER BY created_at DESC",
              (r["id"], r["thread_id"]))
    if it and it["title"]:
        return short(it["title"])
    g = _one(conn, "SELECT text FROM goals WHERE plan_run=? OR draft_run=? OR thread_id=?",
             (r["id"], r["id"], r["thread_id"]))
    if g and g["text"]:
        return short(g["text"])
    th = _one(conn, "SELECT title FROM threads WHERE id=?", (r["thread_id"],))
    if th and th["title"]:
        return short(th["title"])
    return short(r["prompt"] if r["provider"] != "component" else "") or "a run"


def stages(root: Path, comp: components.Component) -> List[str]:
    """The titles of the workflow stages this card feeds, as the Workflow page draws them."""
    try:
        eki = projects.is_self(root)
        wf, _, _ = api_workflow._card(root)
        v = api_workflow._values(wf.spec if wf else {}, eki)
        names = {s["id"]: s["title"] for s in api_workflow.stages(v, eki)}
        fed = api_workflow.feeds({comp.name: comp}, v)
    except Exception:  # a broken workflow card leaves the stages empty
        return []
    return [names.get(s, s) for f in fed if f["card"] == comp.name for s in f["to"]]


def _called(conn: sqlite3.Connection, root: Path, comp: components.Component) -> List[Tuple[float, str]]:
    """(when, run id) for each use: the caller of a call, else the call; serve links' runs too."""
    out = [(c["when"], c["run"] if c["caller"] == "you" else c["caller"])
           for c in uses.calls(conn, comp.name, root, limit=500)]
    if comp.kind == "serve":
        for st in servers.states():
            if st.get("name") != comp.name or st.get("folder") != str(comp.folder):
                continue
            for rid, t in ((st.get("by"), st.get("started_at")), (st.get("last_run"), st.get("last_call_at"))):
                if rid and isinstance(t, (int, float)):
                    out.append((float(t), rid))
    return out


def _ids(conn: sqlite3.Connection, root: Path) -> Tuple[List[str], bool]:
    """The project ids whose folder is root, and whether root is eki itself (its goals have no project)."""
    root_s = str(root)
    ids = [r["id"] for r in conn.execute("SELECT id FROM projects WHERE path=? OR folder=?",
                                         (root_s, root_s)).fetchall()]
    try:
        me = projects.is_self(root)
    except Exception:
        me = False
    return ids + ([projectchats.SELF] if me else []), me


def _passive(conn: sqlite3.Connection, root: Path) -> List[Tuple[float, str]]:
    """(when, run id) for the project's recent runs that aren't calls or commands, newest first."""
    pids, me = _ids(conn, root)
    marks = ",".join("?" * len(pids)) or "NULL"
    goal = f"(g.project IN ({marks}){' OR g.project IS NULL' if me else ''})"
    root_s = str(root)
    rows = conn.execute(
        "SELECT r.id, r.created_at FROM runs r JOIN threads t ON t.id=r.thread_id "
        "WHERE COALESCE(r.provider, '') NOT IN ('component', 'command') AND ("
        f"t.project IN ({marks}) OR t.cwd=? OR t.cwd LIKE ? OR r.thread_id IN ("
        f"SELECT i.thread_id FROM items i JOIN goals g ON g.id=i.goal_id WHERE {goal}) "
        f"OR r.thread_id IN (SELECT g.thread_id FROM goals g WHERE {goal})) "
        "ORDER BY r.created_at DESC, r.rowid DESC LIMIT 500",
        (*pids, root_s, root_s.rstrip("/") + "/%", *pids, *pids)).fetchall()
    return [(r["created_at"], r["id"]) for r in rows]


def _midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _who(sorts: List[str]) -> str:
    """'build agents', 'build agents and planners': the sorts by how often, at most two."""
    count: Dict[str, int] = {}
    for s in sorts:
        count[s] = count.get(s, 0) + 1
    top = sorted(count, key=lambda s: -count[s])[:2]
    return " and ".join(_PLURAL.get(s, s) for s in top)


def used_by(conn: sqlite3.Connection, root: Path, comp: components.Component,
            now: Optional[float] = None) -> Dict[str, Any]:
    """{"stages", "line", "runs"}; empty parts when the runs can't be read."""
    out: Dict[str, Any] = {"stages": stages(root, comp), "line": "not used yet", "runs": []}
    now = now or time.time()
    try:
        uses_ = _called(conn, root, comp) if comp.kind in CALLED else _passive(conn, root)
    except Exception:  # a broken DB: the stages still say where it goes
        return out
    if not uses_:
        return out
    uses_.sort(key=lambda u: -u[0])
    rows: Dict[str, Optional[sqlite3.Row]] = {}
    for _, rid in uses_:
        if rid not in rows:
            rows[rid] = _run(conn, rid)
    seen: List[str] = []
    for when, rid in uses_:
        r = rows[rid]
        if r is None or rid in seen:
            continue
        seen.append(rid)
        out["runs"].append({"run": rid, "title": title(conn, r), "when": when})
        if len(out["runs"]) >= NEWEST:
            break
    today = [u for u in uses_ if u[0] >= _midnight(now)]
    sorts = [sort_of(conn, rows[rid]) if rows[rid] is not None else "you" for _, rid in today]
    last = out["runs"][0] if out["runs"] else None
    head = f"used {len(today)}× today by {_who(sorts)}" if today else "not used today"
    out["line"] = head + (f"; last by '{last['title']}' {ago(last['when'], now)}" if last else "")
    return out
