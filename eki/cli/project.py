# SPDX-License-Identifier: Apache-2.0
"""`eki project`: a folder with .eki/ in it — calls made inside belong to it."""
from __future__ import annotations

import os
import sys

from .. import projects
from .common import call

ORDER = 40


def register(sub) -> None:
    pj = sub.add_parser("project", help="a folder with .eki/ in it: calls made inside belong to it")
    pj.add_argument("action", nargs="?", default="show", choices=["show", "init"])
    pj.add_argument("folder", nargs="?", default="", help="default: here")
    pj.add_argument("--name", default="", help="init: what to call it (default: the folder's name)")
    pj.add_argument("-n", "--limit", type=int, default=15)
    pj.set_defaults(func=cmd_project)


def cmd_project(args) -> int:
    """Make a folder a project, or say which project a folder is in and
    what was asked there."""
    folder = os.path.abspath(os.path.expanduser(args.folder or "."))
    if args.action == "init":
        try:
            p = projects.init(folder, args.name)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"{'made' if p['made'] else 'already'} a project: {p['name']} ({p['root']})")
        print("calls made in this folder, or any folder inside it, belong to it")
        return 0
    about = projects.describe(folder)
    if not about:
        print("not in a project (no .eki/ here or above) — eki project init makes one")
        return 1
    print(f"{about['name']}  {about['root']}")
    rows = call("GET", "/api/runs", args.service, params={"limit": args.limit, "project": about["root"]})
    for r in rows:
        prompt = (r["prompt"] or "").replace("\n", " ")[:50]
        print(f"  {r['id']}  {r['state']:<11} {r['backend'] or '-':<7} {prompt}")
    if not rows:
        print("  nothing asked here yet")
    return 0
