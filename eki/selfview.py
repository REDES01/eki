"""The `eki self` board's words, as data: the CLI prints them, the web shows them."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from . import asks, checkslots, doccheck, queue, selfwork, store


def board(c: sqlite3.Connection) -> Dict[str, Any]:
    """Settings, the queue in order (then the side path), and the last 20 goals with their items."""
    s = selfwork.settings()
    return {"autonomy": s["autonomy"], "parallel": s["parallel"], "source": str(selfwork.source()),
            "queue": [_lined(c, n, it) for n, it in enumerate(lined(c), 1)],
            "goals": [_goal(c, g) for g in goals(c)]}


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
    return {"id": g["id"], "state": g["state"], "text": text,
            "wish": (text.splitlines() or [""])[0], "error": g["error"],
            "drafting": drafting(c, g) if g["state"] == "drafting" else None,
            "created_at": g["created_at"], "draft_run": g["draft_run"], "plan_run": g["plan_run"],
            "thread": _thread(c, g["draft_run"]) or _thread(c, g["plan_run"]),
            "items": [_item(c, it) for it in items(c, g["id"])]}


def _item(c, it) -> Dict[str, Any]:
    return {"id": it["id"], "state": it["state"], "title": it["title"], "where": where(it),
            "note": note(c, it), "docs_only": bool(doccheck.of_item(it)), "run": it["run_id"],
            "thread": _thread(c, it["run_id"]), "branch": it["branch"]}


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
    run = store.run(c, it["gate2_run"]) if it["gate2_run"] else None
    if run is None:
        return "gate 2 starting"
    if checkslots.waiting(c, run):
        return f"waiting for a check slot (run {run['id']})"
    if run["state"] in store.ACTIVE:
        return f"gate 2 (run {run['id']})"
    return f"gate 2 {run['state']} (run {run['id']})"


def where(it) -> str:
    if it["state"] in ("building", "judging", "resolving", "rechecking") and it["run_id"]:
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
    n = said(it)
    return " · ".join(marks + [n] if n else marks)


def said(it) -> str:
    state = it["state"]
    if state in ("left", "unfit", "rolled back") and it["error"]:
        return f"! {it['error'].splitlines()[0][:150]}"
    if state == "proposed":
        files = json.loads(it["touched"] or "[]")
        if selfwork.settings().get("autonomy") == "apply":
            return f"✓ {len(files)} files; joins the queue"
        return f"✓ {len(files)} files; `eki self apply {it['id']}` queues it"
    if state == "locked":
        files = ", ".join(json.loads(it["locked"] or "[]")) or "?"
        return f"! touches locked files: {files}; `eki self apply {it['id']} --yes` if you agree"
    if state == "landed":
        return f"✓ in integration main ({(it['rebased'] or it['commit_sha'] or '')[:12]})"
    if state == "live":
        return f"✓ live in build {it['build'] or '?'}"
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
