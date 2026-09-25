# SPDX-License-Identifier: Apache-2.0
"""`eki routing`: the routing table; explain a request; replay recent ones."""
from __future__ import annotations

import os
import sys

from .common import call

ORDER = 140


def register(sub) -> None:
    ro = sub.add_parser("routing", help="the routing table; explain a request; replay recent ones")
    ro.add_argument("action", nargs="?", default="show", choices=["show", "explain", "replay", "undo", "forget"])
    ro.add_argument("rest", nargs="*")
    ro.add_argument("-f", "--folder", default="",
                    help="as a request in this folder (a project's .eki/routing.yaml applies); "
                         "show: default here")
    ro.set_defaults(func=cmd_routing)


def cmd_routing(args) -> int:
    """The routing table, and checking it: explain a request, replay recent ones."""
    act, rest = args.action, " ".join(args.rest)
    if act == "explain":
        if not rest:
            print('eki routing explain "your request"', file=sys.stderr)
            return 1
        folder = os.path.abspath(os.path.expanduser(args.folder)) if args.folder else ""
        d = call("POST", "/api/routing/explain", args.service, json={"prompt": rest, "folder": folder})
        lab = d["label"]
        print(f"prompt check : {lab['task']} · {lab['difficulty']} ({lab['source']}) → row “{d['row_title']}” — {d['row_why']}")
        print(f"that row     : {' → '.join(d['row_targets']) or '(empty)'}   [{d['row_source']}]")
        print(f"goes to      : {d['choice'] or 'nothing'} — {d['reason']}")
        for r in d.get("rejected") or []:
            print(f"   not {r}")
        return 0
    if act == "replay":
        rows = call("GET", f"/api/routing/replay?limit={int(rest or 40)}", args.service)
        diff = [r for r in rows if r["differs"]]
        for r in rows:
            mark = "≠" if r["differs"] else " "
            print(f"{mark} {r['then']:<14} → {r['now']:<28} {r['row']:<26} {r['prompt'][:60]}")
        print(f"\n{len(diff)} of {len(rows)} recent requests would go somewhere else now")
        return 0
    if act in ("undo", "forget"):
        got = call("POST", f"/api/routing/forget/{rest or 'all'}", args.service)
        print(("taken back: " + ", ".join(got["removed"])) if got["removed"] else "no rule like that")
        return 0
    # shown from inside a project, the table is that project's (its .eki/routing.yaml)
    here = os.path.abspath(os.path.expanduser(args.folder or "."))
    print(call("GET", "/api/routing", args.service, params={"folder": here})["text"])
    return 0
