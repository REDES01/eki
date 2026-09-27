"""A plan run's answer becomes items (docs/self-build.md, "Items").

`conclude_plan` rechecks the goal's state under a transaction, so a restart
mid-plan just waits for the run and a goal is moved on exactly once.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List

from . import db, plangraph, projects, selfbrief, store, workspace


def conclude_plan(conn: sqlite3.Connection, g: sqlite3.Row) -> List[str]:
    from . import selfwork              # selfwork calls this from its tick: import it late
    run = store.run(conn, g["plan_run"]) if g["plan_run"] else None
    if run is None or run["state"] in store.ACTIVE:
        return []
    with db.tx(conn):
        cur = conn.execute("SELECT state FROM goals WHERE id=?", (g["id"],)).fetchone()
        if cur["state"] != "planning":
            return []
        if run["state"] != "done":
            err = f"the plan run ended {run['state']}: {run['error'] or ''}".strip()
            conn.execute("UPDATE goals SET state='failed', error=? WHERE id=?", (err, g["id"]))
            return [f"goal {g['id']}: {err}"]
        answer = store.answer(conn, run["id"])
        try:                            # a standing round may find nothing worth doing now
            planned = selfbrief.items_in(answer, allow_empty=bool(g["standing_id"]))
        except ValueError as e:
            conn.execute("UPDATE goals SET state='failed', error=? WHERE id=?", (str(e), g["id"]))
            return [f"goal {g['id']}: {e}"]
        ids: Dict[str, str] = {}
        for it in planned:
            ids[it["title"]] = selfwork.new_item(conn, g["id"], it["title"], it["spec"], it["files"], [],
                                                 it["independent"])
        for it in planned:
            deps = [ids[d] for d in it["deps"] if d in ids and ids[d] != ids[it["title"]]]
            selfwork._set(conn, ids[it["title"]], deps=db.dumps(deps))
        why = None if planned else rest_why(answer)
        conn.execute("UPDATE goals SET state='planned', shape=?, error=COALESCE(?, error) WHERE id=?",
                     (json.dumps(plangraph.shape(planned)), why, g["id"]))
    workspace.remove(projects.repo_for(conn, g), f"plan-{g['id']}", delete_branch=True)
    if why:
        return [f"goal {g['id']}: nothing worth doing now — {why}"]
    return [f"goal {g['id']}: {len(planned)} item(s) planned"]


def rest_why(answer: str) -> str:
    """The plan's last non-empty line before its `ITEMS:` line: why nothing is worth doing."""
    lines = (answer or "").splitlines()
    marks = [i for i, ln in enumerate(lines) if re.match(r"^\s*`?ITEMS:", ln)]
    before = lines[:marks[-1]] if marks else lines
    said = [ln.strip(" `") for ln in before if ln.strip(" `")]
    return said[-1][:300] if said else "the plan found nothing worth doing now"
