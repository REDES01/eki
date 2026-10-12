"""Checks and verdicts: what a goal must show, and what an independent judge saw.

A goal's checks are written by its drafter, under a `Checks` heading as lines
`C<n>: <statement>`, and stored with the goal (`store_checks`, the `checks`
table). A judge run is registered in `judgings` with the checks it may judge
(`register`); it files one verdict per check through `eki verdict`, which
calls `submit`. Nothing else becomes a verdict: a builder's run is never in
`judgings`, so its `SUMMARY:` or `ITEM: done` can't close a check.

A verdict has one fixed shape (`KEYS`, `validate`). Within a round a check's
result is the worst of its verdicts (failed > uncertain > passed); a `second`
run's verdicts stand over the round's others, and `final`/`play` over `item`.
`lines` and `view` are the one source for the command line and the web.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import db, store

KEYS = ("check_id", "verdict", "evidence", "reasoning", "counter_evidence", "limitation", "suggestion")
VERDICTS = ("passed", "failed", "uncertain")
SCOPES = ("item", "final", "second", "play")
#: the item-only check: does the diff do what the item's spec says
SPEC = "spec"
MAX_CHARS = 4000
#: worse first
RANK = {"failed": 0, "uncertain": 1, "passed": 2}
#: which scope's verdicts stand within a round, highest first
TIER = {"second": 0, "final": 1, "play": 1, "item": 2}
TEXT_SHOWN = 60
FORM = ('{"check_id": "c1", "verdict": "passed|failed|uncertain", "evidence": "<what you ran and saw, '
        'or file:line>", "reasoning": "<why that settles it>", "counter_evidence": "<what speaks '
        'against it, or none found>", "limitation": "<what you could not verify and why>", '
        '"suggestion": "<a concrete fix; required when failed or uncertain>"}')

_HEADING = re.compile(r"^\s*(?:#+\s*|\*\*)?\s*Checks\s*(?:\*\*)?\s*:?\s*(?:\*\*)?\s*$", re.I)
_OTHER = re.compile(r"^\s*(#+\s|\*\*[^*]+\*\*\s*:?\s*$|(What must be true when done|Read first|"
                    r"Item shape and shared files|Tests \(bin/check green\))\s*:?\s*$)", re.I)
_CHECK = re.compile(r"^\s*(?:[-*]\s*)?`?\**C(\d+)\**`?\s*:\s*(.+?)\s*$")


# ---- the checks -------------------------------------------------------------------------------

def checks_in(text: str) -> List[Tuple[str, str]]:
    """[("c1", "…"), …] from `C<n>:` lines under a `Checks` heading; every other line is ignored."""
    out: List[Tuple[str, str]] = []
    seen = set()
    under = False
    for ln in (text or "").splitlines():
        if _HEADING.match(ln):
            under = True
            continue
        if under and _OTHER.match(ln):
            under = False
        if not under:
            continue
        m = _CHECK.match(ln)
        if m and f"c{int(m.group(1))}" not in seen:
            cid = f"c{int(m.group(1))}"
            seen.add(cid)
            out.append((cid, m.group(2).strip()))
    return out


def store_checks(conn: sqlite3.Connection, gid: str, checks: Iterable[Tuple[str, str]]) -> None:
    """The goal's checks, in the caller's tx (the one that writes goals.text); replaces any before."""
    conn.execute("DELETE FROM checks WHERE goal_id=?", (gid,))
    t = db.now()
    for n, (cid, text) in enumerate(checks, 1):
        conn.execute("INSERT OR REPLACE INTO checks(goal_id, id, n, text, created_at) VALUES (?,?,?,?,?)",
                     (gid, str(cid).lower(), n, str(text), t))


def checks_of(conn: sqlite3.Connection, gid: str) -> List[sqlite3.Row]:
    """The goal's checks in order ([] for a goal that has none)."""
    return conn.execute("SELECT * FROM checks WHERE goal_id=? ORDER BY n", (gid,)).fetchall()


# ---- one verdict --------------------------------------------------------------------------------

def validate(record: Any) -> List[str]:
    """What is wrong with `record` as a verdict ([] when nothing)."""
    if not isinstance(record, dict):
        return ["a verdict is a JSON object"]
    out = [f"unknown key {k!r}" for k in record if k not in KEYS]
    out += [f"missing key {k!r}" for k in KEYS if k not in record]
    for k in KEYS:
        if k in record and not isinstance(record[k], str):
            out.append(f"{k} must be a string")
        elif k in record and len(record[k]) > MAX_CHARS:
            out.append(f"{k} is over {MAX_CHARS} characters")
    if out:
        return out
    said = {k: record[k].strip() for k in KEYS}
    v = said["verdict"]
    if not said["check_id"]:
        out.append("check_id is empty")
    if v not in VERDICTS:
        out.append(f"verdict must be one of {', '.join(VERDICTS)}, not {v!r}")
    for k in ("evidence", "reasoning"):
        if not said[k]:
            out.append(f"{k} is empty")
    if v == "uncertain" and not said["limitation"]:
        out.append("an uncertain verdict needs a limitation")
    if v in ("failed", "uncertain") and not said["suggestion"]:
        out.append(f"{'an' if v == 'uncertain' else 'a'} {v} verdict needs a suggestion")
    return out


def register(conn: sqlite3.Connection, run_id: str, goal_id: str, item_id: Optional[str], scope: str,
             checks: Iterable[str], round: int = 1, avoid: Iterable[str] = ()) -> None:
    """Make `run_id` a judge run for `checks` (in the caller's tx, with the run's creation)."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}")
    ids = list(dict.fromkeys(str(c).lower() for c in checks))
    conn.execute("INSERT OR REPLACE INTO judgings(run_id, goal_id, item_id, scope, checks, round, avoid, "
                 "created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, goal_id, item_id, scope, db.dumps(ids), int(round), db.dumps(sorted(set(avoid))),
                  db.now()))


def judging(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM judgings WHERE run_id=?", (run_id,)).fetchone()


def listed(j: sqlite3.Row) -> List[str]:
    """The check ids a judging row covers."""
    try:
        got = json.loads(j["checks"] or "[]")
    except ValueError:
        return []
    return [str(c) for c in got] if isinstance(got, list) else []


def submit(conn: sqlite3.Connection, run_id: str, record: Any) -> str:
    """File one verdict from judge run `run_id`; its id. ValueError with every problem joined by '; '."""
    problems = validate(record)
    if problems:
        raise ValueError("; ".join(problems))
    cid = record["check_id"].strip().lower()
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        vid = _file(conn, run_id, cid, record)
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise
    if own:
        conn.execute("COMMIT")
    return vid


def _file(conn: sqlite3.Connection, run_id: str, cid: str, record: Dict[str, str]) -> str:
    j = judging(conn, run_id)
    run = store.run(conn, run_id) if run_id else None
    problems = []
    if j is None:
        problems.append(f"run {run_id or '(none)'} is not a judge run")
    if run is None or run["id"] != run_id:
        problems.append(f"no run {run_id or '(none)'}")
    elif run["state"] not in store.ACTIVE:
        problems.append(f"run {run_id} has ended ({run['state']})")
    if j is not None and cid not in listed(j):
        problems.append(f"check {cid} is not one this run judges ({', '.join(listed(j)) or 'none'})")
    if j is not None and conn.execute("SELECT 1 FROM verdicts WHERE run_id=? AND check_id=?",
                                      (run_id, cid)).fetchone():
        problems.append(f"this run has already filed a verdict for {cid}")
    if problems:
        raise ValueError("; ".join(problems))
    vid = store.new_id()
    conn.execute("INSERT INTO verdicts(id, run_id, goal_id, item_id, check_id, scope, round, verdict, evidence, "
                 "reasoning, counter_evidence, limitation, suggestion, provider, model, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (vid, run_id, j["goal_id"], j["item_id"], cid, j["scope"], j["round"],
                  record["verdict"].strip(), record["evidence"].strip(), record["reasoning"].strip(),
                  record["counter_evidence"].strip(), record["limitation"].strip(),
                  record["suggestion"].strip(), run["provider"], run["model"], db.now()))
    return vid


# ---- reading ----------------------------------------------------------------------------------

def outcome(conn: sqlite3.Connection, run_id: str) -> Dict[str, sqlite3.Row]:
    """{check id: verdict row} the run filed."""
    return {r["check_id"]: r for r in conn.execute("SELECT * FROM verdicts WHERE run_id=?", (run_id,))}


def worst(rows: Iterable[sqlite3.Row]) -> Optional[sqlite3.Row]:
    """The worst of `rows` (failed > uncertain > passed), the newest among equals; None for none."""
    best: Optional[sqlite3.Row] = None
    for r in rows:
        if best is None or (RANK.get(r["verdict"], 1), -r["created_at"]) < (RANK.get(best["verdict"], 1),
                                                                            -best["created_at"]):
            best = r
    return best


def latest(conn: sqlite3.Connection, gid: str) -> Dict[str, sqlite3.Row]:
    """{check id: the verdict that stands now}: in the newest round, the highest scope tier's worst."""
    by: Dict[str, List[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM verdicts WHERE goal_id=? AND check_id!=?", (gid, SPEC)):
        by.setdefault(r["check_id"], []).append(r)
    out: Dict[str, sqlite3.Row] = {}
    for cid, rows in by.items():
        top = max(r["round"] for r in rows)
        rows = [r for r in rows if r["round"] == top]
        tier = min(TIER.get(r["scope"], 2) for r in rows)
        got = worst(r for r in rows if TIER.get(r["scope"], 2) == tier)
        if got is not None:
            out[cid] = got
    return out


def history(conn: sqlite3.Connection, gid: str, cid: str) -> List[sqlite3.Row]:
    """Every verdict for one check of the goal, newest first."""
    return conn.execute("SELECT * FROM verdicts WHERE goal_id=? AND check_id=? ORDER BY created_at DESC, "
                        "rowid DESC", (gid, cid.lower())).fetchall()


def _cut(text: str, n: int = TEXT_SHOWN) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[:n - 1].rstrip() + "…"


def view(conn: sqlite3.Connection, gid: str) -> List[Dict[str, Any]]:
    """One dict per check: {id, text, verdict, scope, round, provider, model, at}; None fields when unjudged."""
    now = latest(conn, gid)
    out = []
    for c in checks_of(conn, gid):
        v = now.get(c["id"])
        out.append({"id": c["id"], "text": c["text"], "verdict": v["verdict"] if v else None,
                    "scope": v["scope"] if v else None, "round": v["round"] if v else None,
                    "provider": v["provider"] if v else None, "model": v["model"] if v else None,
                    "at": v["created_at"] if v else None})
    return out


def lines(conn: sqlite3.Connection, gid: str) -> List[str]:
    """One line per check, `c2  passed   <text>  (final · codex · round 1)`, or `—` when not judged."""
    got = view(conn, gid)
    if not got:
        return ["no checks — not judged"]
    out = []
    for c in got:
        if c["verdict"] is None:
            out.append(f"{c['id']:<4}{'—':<8} {_cut(c['text'])}")
        else:
            out.append(f"{c['id']:<4}{c['verdict']:<8} {_cut(c['text'])}  "
                       f"({c['scope']} · {c['provider'] or '?'} · round {c['round']})")
    return out
