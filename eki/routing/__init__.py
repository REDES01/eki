"""Where a run goes, and why — in that order:

1. you picked a provider (`--to`): it goes there, or waits for it;
2. the thread already has one: it stays, unless that one can't take it;
3. the prompt check picks a row, and the row's first available target
   takes it. Moving down the row is failover.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .. import capacity, paths, providers, store
from .check import check
from .table import row as table_row


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
        if not ok and cfg.get("kind") == "local" and cfg.get("serve") and run["provider"] not in skip:
            return Decision(run["provider"], "picked", f"you picked {run['provider']} (starting it)")
        return Decision(run["provider"] if ok else None, "picked",
                        f"you picked {run['provider']}" + ("" if ok else f" — waiting: {why}"))

    thread = store.thread(conn, run["thread_id"])
    if thread and thread["provider"] and not run["row"]:
        ok, why = can(thread["provider"])
        if ok:
            return Decision(thread["provider"], "thread", f"thread stays with {thread['provider']}")
        note = f"{thread['provider']} can't take it ({why}); "
    else:
        note = ""

    if run["row"]:
        key, reason = run["row"], (run["why"] or f"row {run['row']}")
    else:
        prev = _previous_answer(conn, run)
        key, reason = check(run["prompt"], previous=prev, cwd=thread["cwd"] if thread else None,
                            checker=checker_name())
    entry = table_row(key)
    refused: List[str] = []
    # a subscription near the end of its plan goes last in its row
    saved = {t: capacity.saving(conn, t) for t in entry["targets"]}
    order = [t for t in entry["targets"] if not saved[t]] + [t for t in entry["targets"] if saved[t]]
    for t in entry["targets"]:
        if saved[t] and len(entry["targets"]) > 1:
            refused.append(f"{t} last ({saved[t]})")
    for target in order:
        ok, why = can(target)
        if ok:
            tail = f"; skipped {', '.join(refused)}" if refused else ""
            return Decision(target, entry["key"], f"{note}{reason} → {entry['key']} → {target}{tail}")
        refused.append(f"{target} ({why})")
    return Decision(None, entry["key"], f"{note}{reason} → {entry['key']}: waiting — "
                    + ", ".join(refused))


def _previous_answer(conn: sqlite3.Connection, run: sqlite3.Row) -> Optional[str]:
    before = [r for r in store.thread_runs(conn, run["thread_id"]) if r["seq"] < run["seq"]]
    return store.answer(conn, before[-1]["id"]) if before else None


def explain(conn: sqlite3.Connection, prompt: str) -> Dict[str, object]:
    """What would happen to a new request, without making a run."""
    key, reason = check(prompt, checker=checker_name())
    entry = table_row(key)
    avail = capacity.status(conn)
    return {"row": entry["key"], "title": entry["title"], "why": reason,
            "targets": [{"name": t, "ok": avail.get(t, (False, "no such provider"))[0],
                         "why": avail.get(t, (False, "no such provider"))[1]}
                        for t in entry["targets"]],
            "providers": sorted(providers.config())}
