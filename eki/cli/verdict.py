"""`eki verdict` — a judge run files its verdicts; the one way a check is judged.

The caller is the run in EKI_RUN, which must be a judge run (eki/verdicts.py,
the `judgings` table) still going.

    eki verdict '<json>'     file one verdict
    eki verdict -            file one verdict read from stdin
    eki verdict --lines      file every `VERDICT: {json}` line on stdin (prose is ignored);
                             exit 1 if any is rejected or none is found
    eki verdict --checks     the checks this run judges, and which are filed
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, List, Tuple

from .. import verdicts
from .common import conn, err

NAME = "verdict"
HELP = "a judge run files its verdict on a check (JSON; --lines, --checks)"
_LINE = re.compile(r"^\s*`?VERDICT:\s*(\{.*\})\s*`?\s*$")


def add(p) -> None:
    p.add_argument("record", nargs="?", help="the verdict as JSON, or - to read it from stdin")
    p.add_argument("--lines", action="store_true", help="file every `VERDICT: {json}` line on stdin")
    p.add_argument("--checks", action="store_true", help="the checks this run judges")


def found(text: str) -> List[str]:
    """The JSON of every `VERDICT: {…}` line in `text`."""
    return [m.group(1) for m in map(_LINE.match, (text or "").splitlines()) if m]


def _file(c, rid: str, raw: str) -> Tuple[bool, str]:
    try:
        record: Any = json.loads(raw)
    except ValueError as e:
        return False, f"not JSON: {e}"
    try:
        vid = verdicts.submit(c, rid, record)
    except ValueError as e:
        return False, str(e)
    return True, f"filed {record['check_id'].strip().lower()} {record['verdict'].strip()} ({vid})"


def _checks(c, rid: str) -> int:
    j = verdicts.judging(c, rid)
    if j is None:
        err(f"eki verdict: run {rid} is not a judge run")
        return 1
    filed = verdicts.outcome(c, rid)
    texts = {r["id"]: r["text"] for r in verdicts.checks_of(c, j["goal_id"])}
    for cid in verdicts.listed(j):
        text = texts.get(cid) or ("the item's spec" if cid == verdicts.SPEC else "")
        mark = f"filed: {filed[cid]['verdict']}" if cid in filed else "not filed"
        print(f"{cid:<6}{mark:<20}{text}")
    return 0


def run(args) -> int:
    rid = os.environ.get("EKI_RUN", "").strip()
    if not rid:
        err("eki verdict: only a judge run files verdicts (EKI_RUN is not set)")
        return 2
    c = conn()
    try:
        if args.checks:
            return _checks(c, rid)
        if args.lines:
            raws = found(sys.stdin.read())
            if not raws:
                err("eki verdict: no `VERDICT: {json}` line found")
                return 1
            bad = 0
            for raw in raws:
                ok, said = _file(c, rid, raw)
                bad += 0 if ok else 1
                print(said) if ok else err(f"eki verdict: rejected: {said}")
            return 1 if bad else 0
        if not args.record:
            err("usage: eki verdict '<json>' | eki verdict - | eki verdict --lines | eki verdict --checks")
            return 2
        raw = sys.stdin.read() if args.record == "-" else args.record
        ok, said = _file(c, rid, raw)
        if not ok:
            err(f"eki verdict: rejected: {said}")
            return 1
        print(said)
        return 0
    finally:
        c.close()
