"""`eki use [name args…]` — call a component; alone, list what's reachable from here."""
from __future__ import annotations

import argparse
import os
import sys

from .. import components, db

NAME = "use"
HELP = "call a component (eki use alone lists what's reachable from here)"


def add(p) -> None:
    p.add_argument("name", nargs="?", help="the component to call")
    p.add_argument("args", nargs=argparse.REMAINDER, help="passed to the component")


def run(args) -> int:
    conn = db.connect()
    if args.name:
        print(f"eki use {args.name}: calls are not built yet", file=sys.stderr)
        return 1
    rows = components.listing(os.getcwd(), conn)
    good = [r for r in rows if not r["error"]]
    for r in good:
        print(f"{r['name']:<16} {r['kind']:<8} {r['scope']:<8} {r['about']}")
    for r in rows:
        if r["error"]:
            print(f"broken: {r['folder']}: {r['error']}", file=sys.stderr)
    if not good:
        print("no components reachable from here")
    return 0
