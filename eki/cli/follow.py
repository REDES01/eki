# SPDX-License-Identifier: Apache-2.0
"""`eki watch|cancel|diff|allow <run>`: one run, by its id."""
from __future__ import annotations

import os
import sys

from .. import grant as grant_mod
from .common import call, watch

ORDER = 60


def register(sub) -> None:
    for name, helptext, func in (("watch", "follow a run", cmd_watch), ("cancel", "stop a run", cmd_cancel),
                                 ("diff", "what a run changed", cmd_diff),
                                 ("allow", "allow what a run was refused, and run it again", cmd_allow)):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id")
        p.set_defaults(func=func)


def cmd_watch(args) -> int:
    return watch(args.id, args.service)


def cmd_cancel(args) -> int:
    out = call("POST", f"/api/runs/{args.id}/cancel", args.service)
    print("cancelled" if out.get("cancelled") else "not running")
    return 0 if out.get("cancelled") else 1


def cmd_allow(args) -> int:
    """Allow what a narrowed run was refused, and run it again; followed
    like an ask. From inside a narrowed run, no more than it has."""
    body = {"parent": grant_mod.from_env().to_json()} if os.environ.get(grant_mod.ENV) else {}
    out = call("POST", f"/api/runs/{args.id}/allow", args.service, json=body)
    if not out.get("run"):
        print("that run wasn't refused anything, or it was allowed already", file=sys.stderr)
        return 1
    return watch(out["run"], args.service)


def cmd_diff(args) -> int:
    out = call("GET", f"/api/runs/{args.id}/diff", args.service)
    print(out["diff"] or "(no changes)")
    return 0
