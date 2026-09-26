"""The routing table, and what would happen to a request — checkable on its own."""
from __future__ import annotations

from typing import Any, Dict

from .. import capacity, paths, routing
from ..routing import needs
from ..routing.table import rows
from .common import conn

NAME = "route"
HELP = "show the routing table, or explain where a request would go"


def add(p) -> None:
    p.add_argument("prompt", nargs="*", help="explain this request (leave out to show the table)")
    p.add_argument("-C", "--cwd", help="explain it as if asked in this folder")
    p.add_argument("--image", action="append", metavar="PATH",
                   help="explain it with this picture attached (repeat for more)")


def target_line(t: Dict[str, Any]) -> str:
    """✓/✗, the name, its can tags, and why when not ok (or when it starts on demand)."""
    line = f"  {'✓' if t['ok'] else '✗'} {t['name']} [{', '.join(t['can'])}]"
    return line + (f"  ({t['why']})" if t["why"] else "")


def run(args) -> int:
    c = conn()
    if args.prompt:
        e = routing.explain(c, " ".join(args.prompt), cwd=args.cwd, attachments=args.image)
        print(f"why:   {e['why']}")
        print(f"row:   {e['row']} — {e['title']}   needs: [{', '.join(e['needs'])}]")
        for t in e["targets"]:
            print(target_line(t))
        return 0
    avail = capacity.status(c)
    print(f"table: {paths.config('routing')}   checker: {routing.checker_name()}\n")
    for r in rows():
        print(f"{r['key']:<8} {r['title']}   needs: [{', '.join(needs.row_needs(r))}]")
        for t in r["targets"]:
            ok, why = avail.get(t, (False, "no such provider"))
            print(f"    {'✓' if ok else '✗'} {needs.describe(t)}" + (f"  ({why})" if why else ""))
    return 0
