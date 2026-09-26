"""What eki wrote down about itself: faults, handoffs, corrections, limits (eki/observe.py)."""
from __future__ import annotations

import time
from typing import Any, Dict

from .. import db, observe
from .common import conn

NAME = "observe"
HELP = "what eki wrote down: faults, handoffs, corrections, limits"


def add(p) -> None:
    p.add_argument("--since", default="24h", help="how far back: 30m, 24h, 7d (default 24h)")
    p.add_argument("--kind", choices=observe.KINDS, help="only this kind ('run' rows only when asked)")
    p.add_argument("--full", action="store_true", help="print a fault's traceback under its line")


def _one_line(text: Any, n: int = 80) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def summary(e: Dict[str, Any]) -> str:
    d, kind = e.get("data") or {}, e["kind"]
    if kind == "fault":
        return _one_line(" ".join(x for x in (d.get("frame") or "(not eki's code)", d.get("exc"),
                                              f"in {d['where']}" if d.get("where") else None) if x))
    if kind == "handoff":
        return _one_line(d.get("reason") or d.get("why") or "-")
    if kind == "correction":
        return "redo" if d.get("redo") else _one_line(d.get("prompt"), 60)
    if kind == "limit":
        return _one_line(d.get("error") or d.get("reason") or "-")
    if kind == "regression":
        return _one_line(f"build {d.get('build') or e.get('build') or '?'} {d.get('verdict') or 'worse'}")
    if kind == "run":
        secs = d.get("seconds")
        return f"{d.get('state', '?')} {secs:.0f}s" if isinstance(secs, (int, float)) else str(d.get("state", "?"))
    return _one_line(d)


def run(args) -> int:
    start = db.now() - observe.parse_since(args.since)
    c = conn()
    rows = observe.entries(c, since=start, kind=args.kind)
    runs = 0
    if args.kind is None:
        runs = sum(1 for e in rows if e["kind"] == "run")
        rows = [e for e in rows if e["kind"] != "run"]
    if not rows:
        print(f"nothing written down since {time.strftime('%a %H:%M', time.localtime(start))}")
    for e in rows:
        print(f"{time.strftime('%a %H:%M', time.localtime(e['t']))}  {e['kind']:<10} "
              f"{e.get('run_id') or '-':<14} {e.get('provider') or '-':<10} {summary(e)}")
        if args.full and e["kind"] == "fault" and (e.get("data") or {}).get("traceback"):
            for line in e["data"]["traceback"].rstrip().splitlines():
                print(f"    {line}")
    if args.kind is None:
        print(f"{runs} run{'s' if runs != 1 else ''} ended in the window")
    return 0
