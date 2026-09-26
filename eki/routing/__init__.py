"""Where a run goes, and why — a stack, each layer saying why:

1. you picked a provider (`--to`): it goes there, or waits for it;
2. the thread already has one: it stays, unless that one can't take the
   request (it lacks what the request needs, or it's at a limit);
3. constraints: what the request needs (the row's needs, plus vision for
   an attached picture) against what each target can do, availability,
   cooldowns and quota;
4. intent: the prompt check picks a row — rules first, a model for the rest;
5. preference and failover: the row's targets in order, a subscription
   being saved goes last, and moving down the row is failover.

A why reads "rule: names a path → code → claude (codex last: five_hour 82%)".
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import capacity, models, paths, providers, store
from . import needs, rules
from .check import check
from .table import row as table_row
from .table import rows as table_rows


@dataclass
class Decision:
    provider: Optional[str]      # None: nothing can take it now
    row: str
    why: str


def checker_name() -> str:
    path = paths.config("routing")
    try:
        return str(json.loads(path.read_text()).get("checker") or "local")
    except (OSError, ValueError):
        return "local"


def decide(conn: sqlite3.Connection, run: sqlite3.Row) -> Decision:
    avail = capacity.status(conn, background=run["priority"] == "background")
    skip = set(store.excluded(run))

    def can(name: str) -> Tuple[bool, str]:
        if name in skip:
            return False, "tried already"
        return avail.get(name, (False, "no such provider"))

    if run["pinned"] and run["provider"]:
        ok, why = can(run["provider"])
        cfg = providers.config().get(run["provider"], {})
        off = not ok or why == capacity.STARTS
        if off and cfg.get("kind") in models.MANAGED_KINDS and cfg.get("serve") and run["provider"] not in skip:
            return Decision(run["provider"], "picked", f"you picked {run['provider']} (starting it)")
        return Decision(run["provider"] if ok else None, "picked",
                        f"you picked {run['provider']}" + ("" if ok else f" — waiting: {why}"))

    thread = store.thread(conn, run["thread_id"])
    cwd = thread["cwd"] if thread else None
    atts = store.attachments(run)
    prev = None if run["row"] else _previous_answer(conn, run)
    pic = None if run["row"] else previous_picture(conn, run["thread_id"], run["seq"])
    note = ""
    ruled = None
    if thread and thread["provider"] and not run["row"]:
        # only the rules and the attachments count here: staying costs no model call
        ruled = rules.match(run["prompt"], previous=prev, cwd=cwd, attachments=atts,
                            keys={r["key"] for r in table_rows()}, last_picture=pic)
        want = needs.request_needs(table_row(ruled[0]) if ruled else {"needs": []}, atts)
        mine = thread["provider"]
        lack = needs.lacking(mine, want)
        ok, why = can(mine)
        if providers.config().get(mine, {}).get("kind") == "comfyui":
            # a picture thread never answers as "thread": the rule's row picks the graph
            if not ruled:
                note = f"{mine} draws pictures, this isn't one; "
        elif lack:
            note = f"{mine} can't take it (needs {', '.join(lack)}); "
        elif ok:
            return Decision(mine, "thread", f"thread stays with {mine}")
        else:
            note = f"{mine} can't take it ({why}); "

    if run["row"]:
        key, reason = run["row"], (run["why"] or f"row {run['row']}")
    elif ruled:
        key, reason = ruled
    else:
        key, reason = check(run["prompt"], previous=prev, cwd=cwd, checker=checker_name(),
                            attachments=atts, last_picture=pic)
    entry = table_row(key)
    want = needs.request_needs(entry, atts)
    targets = list(entry["targets"])
    # a subscription near the end of its plan goes last in its row
    saved = {t: capacity.saving(conn, t) for t in targets}
    order = [t for t in targets if not saved[t]] + [t for t in targets if saved[t]]
    last = "".join(f" ({t} last: {saved[t]})" for t in targets if saved[t]) if len(targets) > 1 else ""
    refused: List[str] = []
    for target in order:
        lack = [] if target in skip else needs.lacking(target, want)
        ok, why = (False, needs.cant(lack)) if lack else can(target)
        if ok:
            tail = f"; skipped {', '.join(refused)}" if refused else ""
            return Decision(target, entry["key"],
                            f"{note}{reason} → {entry['key']} → {target}{last}{tail}")
        refused.append(f"{target}: {why}")
    return Decision(None, entry["key"], f"{note}{reason} → {entry['key']}: waiting — "
                    + ", ".join(refused))


def _previous_answer(conn: sqlite3.Connection, run: sqlite3.Row) -> Optional[str]:
    before = [r for r in store.thread_runs(conn, run["thread_id"]) if r["seq"] < run["seq"]]
    return store.answer(conn, before[-1]["id"]) if before else None


def previous_picture(conn: sqlite3.Connection, tid: str, seq: int) -> Optional[str]:
    """The picture the thread's previous run drew or was given, or None
    (an older picture doesn't make a follow-up an edit)."""
    before = [r for r in store.thread_runs(conn, tid) if r["seq"] < seq]
    pic = store.last_picture(conn, tid, seq) if before else None
    if pic is None:
        return None
    last = before[-1]
    drawn = [json.loads(e["data"] or "{}") for e in store.events_after(conn, last["id"])
             if e["kind"] == "tool"]
    mine = store.attachments(last) + [str(d.get("path")) for d in drawn if d.get("name") == "image"]
    return pic if pic in mine else None


def explain(conn: sqlite3.Connection, prompt: str, *, cwd: Optional[str] = None,
            attachments: Optional[List[str]] = None,
            thread_id: Optional[str] = None) -> Dict[str, Any]:
    """What would happen to a new request, without making a run: the row,
    the rule or model's reason, the request's needs, and for each target
    whether it may take it and why not. With a thread, as its next request."""
    pic = None
    if thread_id:
        nxt = max([r["seq"] for r in store.thread_runs(conn, thread_id)] or [0]) + 1
        pic = previous_picture(conn, thread_id, nxt)
    key, reason = check(prompt, cwd=cwd, checker=checker_name(), attachments=attachments,
                        last_picture=pic)
    entry = table_row(key)
    want = needs.request_needs(entry, attachments)
    avail = capacity.status(conn)
    cfgs = providers.config()
    targets = []
    for t in entry["targets"]:
        lack = needs.lacking(t, want)
        ok, why = (False, needs.cant(lack)) if lack else avail.get(t, (False, "no such provider"))
        cfg = cfgs.get(t)
        targets.append({"name": t, "ok": bool(ok), "why": why,
                        "can": providers.capabilities(t, cfg) if cfg is not None else [],
                        "about": str((cfg or {}).get("about") or "")})
    return {"row": entry["key"], "title": entry["title"], "why": reason, "needs": want,
            "targets": targets, "providers": sorted(cfgs)}
