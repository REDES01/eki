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
        if not ok and cfg.get("kind") in models.MANAGED_KINDS and cfg.get("serve") and run["provider"] not in skip:
            return Decision(run["provider"], "picked", f"you picked {run['provider']} (starting it)")
        return Decision(run["provider"] if ok else None, "picked",
                        f"you picked {run['provider']}" + ("" if ok else f" — waiting: {why}"))

    thread = store.thread(conn, run["thread_id"])
    cwd = thread["cwd"] if thread else None
    atts = store.attachments(run)
    prev = None if run["row"] else _previous_answer(conn, run)
    note = ""
    if thread and thread["provider"] and not run["row"]:
        # only the rules and the attachments count here: staying costs no model call
        ruled = rules.match(run["prompt"], previous=prev, cwd=cwd, attachments=atts,
                            keys={r["key"] for r in table_rows()})
        want = needs.request_needs(table_row(ruled[0]) if ruled else {"needs": []}, atts)
        mine = thread["provider"]
        lack = needs.lacking(mine, want)
        ok, why = can(mine)
        if lack:
            note = f"{mine} can't take it (needs {', '.join(lack)}); "
        elif ok:
            return Decision(mine, "thread", f"thread stays with {mine}")
        else:
            note = f"{mine} can't take it ({why}); "

    if run["row"]:
        key, reason = run["row"], (run["why"] or f"row {run['row']}")
    else:
        key, reason = check(run["prompt"], previous=prev, cwd=cwd, checker=checker_name(),
                            attachments=atts)
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


def explain(conn: sqlite3.Connection, prompt: str, *, cwd: Optional[str] = None,
            attachments: Optional[List[str]] = None) -> Dict[str, Any]:
    """What would happen to a new request, without making a run: the row,
    the rule or model's reason, the request's needs, and for each target
    whether it may take it and why not."""
    key, reason = check(prompt, cwd=cwd, checker=checker_name(), attachments=attachments)
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
