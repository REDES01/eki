"""The costliest clusters in the journal: what eki's self-picker works on
after faults (docs/self-build.md, "The loop: eki picks its own work").

Two kinds of cluster, both keyed by a routing row:
- ("handoff", row): runs on `row` that were handed off, where the row has a
  local target able to do what it needs — work that could have stayed local;
- ("correction", row): you said the answer was wrong (or asked again), keyed
  by the row of the run you corrected.

A cluster whose goal (pick_key "journal:<key>") is still open, or landed in
the last 7 days, is left out; after an older landing only newer rows count.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import db, faults, observe, providers, selfwork
from .routing import needs, table

DAY = 86400.0
RECENT = 7 * DAY            # a cluster whose goal landed this recently isn't picked again
EXAMPLES = 5
EXAMPLE_MAX = 200
#: on a tie in count, a correction comes first
ORDER = {"correction": 0, "handoff": 1}


@dataclass
class Cluster:
    kind: str
    row: str
    count: int
    examples: List[Tuple[str, str]] = field(default_factory=list)
    key: str = ""


def local_rows() -> Set[str]:
    """Rows with a target that is a local model, on, and able to do what the row needs."""
    cfgs = providers.config()
    out: Set[str] = set()
    for r in table.rows():
        want = set(needs.row_needs(r))
        for t in r.get("targets") or []:
            cfg = cfgs.get(t)
            if (cfg and cfg.get("kind") == "local" and not cfg.get("off")
                    and want <= set(providers.capabilities(t, cfg))):
                out.add(r["key"])
                break
    return out


def _floor(conn: sqlite3.Connection, key: str, until: float) -> Optional[float]:
    """None when the cluster is blocked; else the time only rows after which count (0 for all)."""
    floor = 0.0
    for g in conn.execute("SELECT id, state FROM goals WHERE pick_key=?", (f"journal:{key}",)):
        if g["state"] in ("drafting", "planning"):
            return None
        for i in conn.execute("SELECT state, COALESCE(landed_at, updated_at) AS moved FROM items"
                              " WHERE goal_id=?", (g["id"],)):
            if i["state"] in faults.OPEN:
                return None
            if i["state"] in faults.LANDED:
                if i["moved"] >= until - RECENT:
                    return None
                floor = max(floor, i["moved"])
    return floor


def _run(conn: sqlite3.Connection, rid: Optional[str]) -> Optional[sqlite3.Row]:
    if not rid:
        return None
    return conn.execute("SELECT id, row, prompt FROM runs WHERE id=?", (rid,)).fetchone()


def clusters(conn: sqlite3.Connection, since: float, until: Optional[float] = None) -> List[Cluster]:
    """Clusters of at least `pick_min_cluster` journal rows in [since, until), best first."""
    until = db.now() if until is None else until
    local = local_rows()
    found: Dict[Tuple[str, str], List[Tuple[float, str, str]]] = {}
    for e in observe.entries(conn, since=since, until=until, kind="handoff"):
        r = _run(conn, e.get("run_id"))
        if r is None or not r["row"] or r["row"] not in local:
            continue
        found.setdefault(("handoff", r["row"]), []).append((e["t"], r["id"], r["prompt"] or ""))
    for e in observe.entries(conn, since=since, until=until, kind="correction"):
        prev = _run(conn, (e.get("data") or {}).get("previous"))
        if prev is None or not prev["row"] or not e.get("run_id"):
            continue
        said = (e.get("data") or {}).get("prompt")
        if said is None:
            said = (_run(conn, e["run_id"]) or {"prompt": ""})["prompt"] or ""
        found.setdefault(("correction", prev["row"]), []).append((e["t"], e["run_id"], said))

    least = int(selfwork.settings().get("pick_min_cluster", 3))
    out: List[Cluster] = []
    for (kind, row), got in found.items():
        key = f"{kind}:{row}"
        floor = _floor(conn, key, until)
        if floor is None:
            continue
        got = [g for g in got if g[0] > floor]
        if len(got) < least:
            continue
        latest = sorted(got, key=lambda g: g[0], reverse=True)[:EXAMPLES]
        examples = [(rid, observe.scrub(p)[:EXAMPLE_MAX]) for _, rid, p in latest]
        out.append(Cluster(kind, row, len(got), examples, key))
    out.sort(key=lambda c: (-c.count, ORDER.get(c.kind, 9), c.row))
    return out


def title(c: Cluster) -> str:
    if c.kind == "handoff":
        return f"make handoffs on row {c.row} stay local"
    return f"fix corrections on row {c.row}"


def goal_text(c: Cluster) -> str:
    """The goal the planner gets for a cluster: what happened, examples, what to do."""
    if c.kind == "handoff":
        what = (f"{c.count} requests routed to row {c.row!r} were handed off from the local model "
                f"in the last 7 days, though the row has a local target able to do what it needs.")
        ask = ("Find why the local model handed these off and change eki so requests like these "
               "stay local and are answered there (the prompt check, the row, the local model's "
               "instructions or the handoff rule), with tests that show it.")
    else:
        what = (f"{c.count} times in the last 7 days you corrected an answer (said it was wrong, "
                f"or asked again) to a request routed to row {c.row!r}.")
        ask = ("Find why these went wrong and change eki so requests like these are routed to the "
               "right row and answered right the first time, with tests that show it.")
    lines = [title(c), "", f"Cluster: {c.kind} on row {c.row}, {c.count} in the last 7 days.",
             what, "", "Examples (run id: prompt):"]
    lines += [f"- {rid}: {' '.join(p.split())}" for rid, p in c.examples]
    lines += ["", ask,
              "Work from the journal, the runs and the code; don't ask a person — decide, and say "
              "what you decided."]
    return "\n".join(lines)


def describe(c: Cluster) -> str:
    """One line: 'handoff on row answer, 4 in 7 days'."""
    return f"{c.kind} on row {c.row}, {c.count} in 7 days"

