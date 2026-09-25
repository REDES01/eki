"""The routing table, and what would happen to a request — checkable on its own."""
from __future__ import annotations

from .. import capacity, paths, routing
from ..routing.table import rows
from .common import conn

NAME = "route"
HELP = "show the routing table, or explain where a request would go"


def add(p) -> None:
    p.add_argument("prompt", nargs="*", help="explain this request (leave out to show the table)")


def run(args) -> int:
    c = conn()
    if args.prompt:
        e = routing.explain(c, " ".join(args.prompt))
        print(f"row:   {e['row']} — {e['title']}")
        print(f"why:   {e['why']}")
        for t in e["targets"]:
            print(f"  {'✓' if t['ok'] else '✗'} {t['name']}" + ("" if t["ok"] else f"  ({t['why']})"))
        return 0
    avail = capacity.status(c)
    print(f"table: {paths.config('routing')}   checker: {routing.checker_name()}\n")
    for r in rows():
        targets = "  ".join(f"{t}{'' if avail.get(t, (False,))[0] else '✗'}" for t in r["targets"])
        print(f"{r['key']:<8} {r['title']}\n         → {targets}")
    return 0
