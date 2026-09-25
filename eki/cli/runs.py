# SPDX-License-Identifier: Apache-2.0
"""`eki runs`: what's running and what finished."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import projects
from .common import call

ORDER = 30


def register(sub) -> None:
    r = sub.add_parser("runs", help="what's running and what finished")
    r.add_argument("-n", "--limit", type=int, default=30)
    r.add_argument("--here", action="store_true", help="only the project this folder is in")
    r.set_defaults(func=cmd_runs)


def cmd_runs(args) -> int:
    query = {"limit": args.limit}
    if args.here:
        here = projects.find(os.getcwd())
        if not here:
            print("not in a project (no .eki/ here or above) — eki project init makes one",
                  file=sys.stderr)
            return 1
        query["project"] = str(here)
    rows = call("GET", "/api/runs", args.service, params=query)
    if not rows:
        print("nothing has run here yet" if args.here else "nothing has run yet")
        return 0
    for r in rows:
        prompt = (r["prompt"] or "").replace("\n", " ")[:44]
        backend = r["backend"] or "-"
        folder = " ⌂" if r.get("cwd") else "  "
        where = "" if args.here or not r.get("project") else f"  [{Path(r['project']).name}]"
        print(f"{r['id']}  {r['state']:<11} {backend:<7}{folder} {prompt}{where}")
    return 0
