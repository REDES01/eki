"""The `eki self` board: goals, their items, and the queue (not a command of its own).
The words are eki/selfview.py's; this prints them."""
from __future__ import annotations

from .. import doccheck, selfview, selfwork
from ..selfview import drafting, stage  # noqa: F401  (said here before they moved)
from .common import ago


def board(c) -> int:
    goals = selfview.goals(c)
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
        for it in selfview.items(c, g["id"]):
            print(f"  {it['id']}  {it['state']:<9} {selfview.where(it):<22} {it['title'][:52]}")
            note = selfview.note(c, it)
            if note:
                print(f"             {note}")
    return 0


def _queue(c) -> None:
    """The queue in order, then the items out of it on the side path (resolving, rechecking)."""
    lined = selfview.lined(c)
    if not lined:
        return
    print("\nqueue")
    for n, it in enumerate(lined, 1):
        pos = f"{n}." if it["state"] == "queued" else "-"
        head = (it["head"] or "")[:8] or "-"
        docs = " (docs only)" if doccheck.of_item(it) else ""
        print(f"  {pos:<3} {it['id']}  on {head:<8}  {stage(c, it):<42} {it['title'][:40]}{docs}")
