"""A plan run's answer becomes items (docs/self-build.md, "Items").

`conclude_plan` feeds plan_ready to the work model (eki/workstep.py), which
rechecks the goal's state under its transaction, so a restart mid-plan just
waits for the run and a goal is moved on exactly once.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List

from . import db, plangraph, projects, selfbrief, store, verdicts, workspace
from .workmodel import Event


def conclude_plan(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    from . import buildfeed, selfwork   # selfwork calls this from its tick: import them late
    run = store.run(conn, g["plan_run"]) if g["plan_run"] else None
    if run is None or run["state"] in store.ACTIVE:
        return []
    d: Dict[str, object] = {"of": "plan", "ended": run["state"], "error": run["error"] or ""}
    planned: List[dict] = []
    if run["state"] == "done":
        answer = store.answer(conn, run["id"])
        d["error"] = None
        try:                            # a standing round may find nothing worth doing now
            planned = selfbrief.items_in(answer, allow_empty=bool(g["standing_id"]),
                                         known=[c["id"] for c in verdicts.checks_of(conn, g["id"])])
        except ValueError as e:
            d["error"] = str(e)
        ids = {it["title"]: store.new_id() for it in planned}
        d["items"] = [{"id": ids[it["title"]], "files": it["files"],
                       "deps": [ids[x] for x in it["deps"] if x in ids and ids[x] != ids[it["title"]]]}
                      for it in planned]
        d["why"] = None if planned else rest_why(answer)

    def made(c: sqlite3.Connection) -> None:
        if d.get("error") is not None or run["state"] != "done":
            return
        for it, p in zip(planned, d["items"]):
            selfwork.new_item(c, g["id"], it["title"], it["spec"], it["files"], p["deps"], it["independent"],
                              context=it.get("context"), iid=p["id"])
            if it.get("checks"):        # the goal checks this item delivers (eki/verdicts.py)
                c.execute("UPDATE items SET checks=? WHERE id=?", (db.dumps(it["checks"]), p["id"]))
        c.execute("UPDATE goals SET shape=?, error=COALESCE(?, error) WHERE id=?",
                  (json.dumps(plangraph.shape(planned)), d["why"], g["id"]))
    said = buildfeed.feed_goal(conn, g, Event("plan_ready", db.now(), d), then=made)
    if said and run["state"] == "done":
        workspace.remove(projects.repo_for(conn, g), f"plan-{g['id']}", delete_branch=True)
    return said


def rest_why(answer: str) -> str:
    """The plan's last non-empty line before its `ITEMS:` line: why nothing is worth doing."""
    lines = (answer or "").splitlines()
    marks = [i for i, ln in enumerate(lines) if re.match(r"^\s*`?ITEMS:", ln)]
    before = lines[:marks[-1]] if marks else lines
    said = [ln.strip(" `") for ln in before if ln.strip(" `")]
    return said[-1][:300] if said else "the plan found nothing worth doing now"
