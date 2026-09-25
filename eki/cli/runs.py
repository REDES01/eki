"""The latest runs and where they stand."""
from __future__ import annotations

from .. import store
from .common import ago, conn

NAME = "runs"
HELP = "list recent runs"


def add(p) -> None:
    p.add_argument("-n", type=int, default=20, help="how many")
    p.add_argument("--active", action="store_true", help="only queued and running")


def run(args) -> int:
    c = conn()
    rows = (store.runs_in(c, store.ACTIVE) if args.active else store.recent_runs(c, args.n))
    if not rows:
        print("no runs")
        return 0
    for r in rows:
        title = " ".join(r["prompt"].split())[:48]
        print(f"{r['id']}  {r['state']:<10} {r['provider'] or '-':<8} {ago(r['created_at']):>8}  {title}")
    return 0
