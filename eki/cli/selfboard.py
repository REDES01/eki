"""The `eki self` board: goals, their items, and the queue (not a command of its own)."""
from __future__ import annotations

import json

from .. import asks, checkslots, doccheck, queue, selfwork, store
from .common import ago


def board(c) -> int:
    goals = c.execute("SELECT * FROM goals ORDER BY created_at DESC LIMIT 20").fetchall()
    if not goals:
        print('nothing yet — `eki self "…"` asks for a change')
        return 0
    s = selfwork.settings()
    print(f"(autonomy {s['autonomy']}, {s['parallel']} at once, source {selfwork.source()})")
    _queue(c)
    for g in goals:
        print(f"\n{g['id']}  {g['state']:<9} {ago(g['created_at']):>8}  {g['text'].splitlines()[0][:70]}")
        if g["state"] == "drafting":
            print(f"           {drafting(c, g)}")
        if g["error"]:
            print(f"           ! {g['error'][:200]}")
        for it in c.execute("SELECT * FROM items WHERE goal_id=? ORDER BY created_at", (g["id"],)):
            print(f"  {it['id']}  {it['state']:<9} {_where(it):<22} {it['title'][:52]}")
            note = _note(c, it)
            if note:
                print(f"             {note}")
    return 0


def drafting(c, g) -> str:
    """A drafting goal's line: its draft run, and the question it is waiting on you for."""
    line = f"drafting  (run {g['draft_run'] or '-'})"
    for a in (asks.for_run(c, g["draft_run"]) if g["draft_run"] else []):
        if a["state"] == "open":
            qs = json.loads(a["payload"] or "{}").get("questions") or [{}]
            q = (qs[0].get("question") or "a question").splitlines()[0][:100]
            return f"{line}\n           asked you: {q} — eki answer {a['id']}"
    return line


def _queue(c) -> None:
    """The queue in order, then the items out of it on the side path (resolving, rechecking)."""
    lined = list(queue.order(c)) + list(selfwork.items_in(c, ("resolving", "rechecking")))
    if not lined:
        return
    print("\nqueue")
    for n, it in enumerate(lined, 1):
        pos = f"{n}." if it["state"] == "queued" else "-"
        head = (it["head"] or "")[:8] or "-"
        docs = " (docs only)" if doccheck.of_item(it) else ""
        print(f"  {pos:<3} {it['id']}  on {head:<8}  {stage(c, it):<42} {it['title'][:40]}{docs}")


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


def _where(it) -> str:
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


def _note(c, it) -> str:
    """The item's second line: its note, with 'docs only' and a held judge run said first."""
    marks = ["docs only"] if doccheck.of_item(it) else []
    if it["state"] == "judging" and it["run_id"]:
        run = store.run(c, it["run_id"])
        if run is not None and checkslots.waiting(c, run):
            marks.append("waiting for a check slot")
    note = _said(it)
    return " · ".join(marks + [note] if note else marks)


def _said(it) -> str:
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
