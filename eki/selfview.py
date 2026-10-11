"""The `eki self` board's words, as data: the CLI prints them, the web shows them."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from . import asks, checkslots, doccheck, notifygoals, projects, queue, selfwork, store


def board(c: sqlite3.Connection) -> Dict[str, Any]:
    """Settings, the queue in order (then the side path), the last 20 goals with their items,
    and each project's open pull requests (read from the db; no gh is run)."""
    s = selfwork.settings()
    return {"autonomy": s["autonomy"], "parallel": s["parallel"], "source": str(selfwork.source()),
            "queue": [_lined(c, n, it) for n, it in enumerate(lined(c), 1)],
            "goals": [_goal(c, g) for g in goals(c)], "prs": projects.open_prs(c)}


def goals(c: sqlite3.Connection) -> List[sqlite3.Row]:
    return c.execute("SELECT * FROM goals ORDER BY created_at DESC LIMIT 20").fetchall()


def items(c: sqlite3.Connection, gid: str) -> List[sqlite3.Row]:
    return c.execute("SELECT * FROM items WHERE goal_id=? ORDER BY created_at", (gid,)).fetchall()


def lined(c: sqlite3.Connection) -> List[sqlite3.Row]:
    """The queue in order, then the items out of it on the side path (resolving, rechecking)."""
    return list(queue.order(c)) + list(selfwork.items_in(c, ("resolving", "rechecking")))


def _lined(c, n: int, it) -> Dict[str, Any]:
    return {"id": it["id"], "pos": n if it["state"] == "queued" else None, "state": it["state"],
            "head": it["head"], "stage": stage(c, it), "title": it["title"],
            "docs_only": bool(doccheck.of_item(it)), "run": it["run_id"],
            "thread": _thread(c, it["run_id"])}


def _goal(c, g) -> Dict[str, Any]:
    text = g["text"] or ""
    return {"id": g["id"], "state": g["state"], "stands": stands(c, g), "text": text,
            "wish": (text.splitlines() or [""])[0], "error": g["error"],
            "drafting": drafting(c, g) if g["state"] == "drafting" else None,
            "created_at": g["created_at"], "draft_run": g["draft_run"], "plan_run": g["plan_run"],
            "thread": _thread(c, g["draft_run"]) or _thread(c, g["plan_run"]),
            "items": [_item(c, it) for it in items(c, g["id"])]}


def stands(c, g) -> str:
    """Where a goal stands: its state, or for a planned goal what its items say
    (for you, in flight, done; planned while it has none)."""
    if g["state"] != "planned":
        return g["state"]
    its = [it for it in items(c, g["id"]) if it["state"] != "dropped"]
    if any(it["state"] in ("locked", "left", "unfit", "rolled back")
           or (it["state"] == "proposed" and not it["pr"]) for it in its):
        return "for you"
    if any(it["state"] in notifygoals.OPEN for it in its):
        return "in flight"
    if not its:
        return "planned"
    return "done" if all(notifygoals.finished(it["state"], it["pr"]) for it in its) else "planned"


def _item(c, it) -> Dict[str, Any]:
    return {"id": it["id"], "state": it["state"], "title": it["title"], "where": where(it),
            "note": note(c, it), "docs_only": bool(doccheck.of_item(it)), "run": it["run_id"],
            "thread": _thread(c, it["run_id"]), "branch": it["branch"], "pr": it["pr"],
            "context": [x["name"] for x in context(it)] or None}


def context(it) -> List[Dict[str, str]]:
    """The knowledge cards the planner picked for the item ({name, why}); [] when none or unreadable."""
    try:
        picked = json.loads(it["context"] or "null")
    except (ValueError, TypeError):
        return []
    if not isinstance(picked, list):
        return []
    return [{"name": str(x["name"]), "why": str(x.get("why") or "")}
            for x in picked if isinstance(x, dict) and x.get("name")]


def _thread(c, run_id: Optional[str]) -> Optional[str]:
    if not run_id:
        return None
    r = c.execute("SELECT thread_id FROM runs WHERE id=?", (run_id,)).fetchone()
    return r["thread_id"] if r else None


def drafting(c, g) -> str:
    """A drafting goal's line: its draft run, and the question it is waiting on you for."""
    line = f"drafting  (run {g['draft_run'] or '-'})"
    for a in (asks.for_run(c, g["draft_run"]) if g["draft_run"] else []):
        if a["state"] == "open":
            qs = json.loads(a["payload"] or "{}").get("questions") or [{}]
            q = (qs[0].get("question") or "a question").splitlines()[0][:100]
            return f"{line}\n           asked you: {q} — eki answer {a['id']}"
    return line


def stage(c, it) -> str:
    """Where a queued (or resolving) item stands, in words."""
    if it["state"] in ("resolving", "rechecking"):
        return f"{it['state']} (run {it['run_id'] or '-'})"
    if not it["rebased"]:
        return "rebasing"
    if it["gate2"] == "green":
        return "gate 2 green — waiting for the ones ahead"
    run = store.run(c, it["gate2_run"]) if it["gate2_run"] and it["gate2_on"] == it["rebased"] else None
    if run is None:
        return _batch_of(c, it)
    if checkslots.waiting(c, run):
        return f"waiting for a check slot (run {run['id']})"
    if run["state"] in store.ACTIVE:
        return f"gate 2 (run {run['id']})"
    return f"gate 2 {run['state']} (run {run['id']})"


def _batch_of(c, it) -> str:
    """For an item without a check of its own: in the batch a check above it covers,
    or waiting for the next batch (it joined after that check started). Only the items
    of its own project (NULL: eki) count: batches never mix projects."""
    lined = c.execute("SELECT i.* FROM items i LEFT JOIN goals g ON g.id=i.goal_id "
                      "WHERE i.state='queued' AND g.project IS ? "
                      "ORDER BY COALESCE(i.queued_at, i.created_at), i.created_at",
                      (_project_id(c, it),)).fetchall()
    ids = [x["id"] for x in lined]
    for x in lined:
        run = store.run(c, x["gate2_run"]) if x["gate2_run"] and x["gate2_on"] == x["rebased"] else None
        if run is not None and run["state"] in store.ACTIVE and it["id"] in ids:
            if ids.index(it["id"]) < ids.index(x["id"]):
                return f"in the batch checked on {x['id']}"
            return f"next batch — joined after the check on {x['id']} started"
    return "waiting for the next batch"


def where(it) -> str:
    if it["state"] == "reviewing":
        return f"review {it['review_run'] or '-'}"
    if it["state"] == "judging" and it["run_id"]:
        both = f" · review run {it['review_run']}" if it["review_run"] else ""
        return f"gate 1 run {it['run_id']}{both}"
    if it["state"] in ("building", "resolving", "rechecking") and it["run_id"]:
        return f"run {it['run_id']}"
    if it["state"] in ("proposed", "locked"):
        return it["branch"] or ""
    if it["state"] == "queued":
        return f"on {(it['head'] or '')[:8]}" if it["head"] else "rebasing"
    if it["state"] == "waiting":
        deps = json.loads(it["deps"] or "[]")
        return f"after {', '.join(deps)}" if deps else "for room"
    return ""


def note(c, it) -> str:
    """The item's second line: its note, with 'docs only' and a held judge run said first."""
    marks = ["docs only"] if doccheck.of_item(it) else []
    if it["state"] == "judging" and it["run_id"]:
        run = store.run(c, it["run_id"])
        if run is not None and checkslots.waiting(c, run):
            marks.append("waiting for a check slot")
    n = said(it, c=c)
    return " · ".join(marks + [n] if n else marks)


def _project_id(c, it) -> Optional[str]:
    g = c.execute("SELECT project FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    return g["project"] if g else None


def _project(c, it) -> Optional[sqlite3.Row]:
    """The project an item's goal works on; None for eki's own items (or with no connection)."""
    pid = _project_id(c, it) if c is not None else None
    return c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone() if pid else None


def said(it, *, c: Optional[sqlite3.Connection] = None) -> str:
    state = it["state"]
    if state == "queued" and (it["why"] or "").startswith("waits: "):
        return it["why"]
    if state in ("left", "unfit", "rolled back") and it["error"]:
        return f"! {it['error'].splitlines()[0][:150]}"
    if state == "reviewing":
        return "a second reader (the reviewer role, Settings → Routing) is reading the diff"
    if state == "judging" and it["review_run"]:
        return "the checks and a second reader run side by side"
    if state == "proposed" and it["pr"]:
        return f"PR open: {it['pr']}"
    if state == "proposed":
        files = json.loads(it["touched"] or "[]")
        got = it["review"] or ""
        if got.startswith("no:"):
            return (f"! the review says no: {got[3:].strip()[:120]}"
                    f" — `eki self apply {it['id']}` if you agree")
        seen = f" · review {got[:80]}" if got else ""
        project = _project(c, it)
        if project is not None:
            if projects.autonomy(project) == "apply":
                return f"✓ {len(files)} files; joins {project['name']}'s queue{seen}"
        elif selfwork.settings().get("autonomy") == "apply":
            return f"✓ {len(files)} files; joins the queue{seen}"
        return f"✓ {len(files)} files; `eki self apply {it['id']}` queues it{seen}"
    if state == "waiting" and (it["review"] or "").startswith("no:"):
        return f"! the review said no: {it['review'][3:].strip()[:120]} — rebuilding once"
    if state == "locked":
        files = ", ".join(json.loads(it["locked"] or "[]")) or "?"
        return f"! touches locked files: {files}; `eki self apply {it['id']} --yes` if you agree"
    if state == "landed":
        return f"✓ in integration main ({(it['rebased'] or it['commit_sha'] or '')[:12]})"
    if state == "live":
        return f"✓ live in build {it['build'] or '?'}"
    if state == "applied" and it["landed_on"]:
        landed = f"✓ landed on {it['landed_on']} ({(it['rebased'] or it['commit_sha'] or '')[:12]})"
        if (it["why"] or "").startswith("after-merge build failed"):
            return f"{landed}; {it['why']}"
        return landed
    if state == "applied":
        return f"✓ in main ({(it['commit_sha'] or '')[:12]})"
    return ""


def verdicts(c: sqlite3.Connection) -> Dict[str, str]:
    """Gate 4 per build: better/same/worse, 'measuring' until judged."""
    try:
        rows = c.execute("SELECT build, verdict FROM build_scores ORDER BY healthy_at").fetchall()
    except Exception:
        return {}
    return {r["build"]: r["verdict"] or "measuring" for r in rows}
