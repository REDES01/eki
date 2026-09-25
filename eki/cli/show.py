"""Everything about one run: where it went and why, attempts, the answer."""
from __future__ import annotations

import json
import time

from .. import store
from .common import conn

NAME = "show"
HELP = "show one run in full: route, reason, attempts, events"


def add(p) -> None:
    p.add_argument("run")
    p.add_argument("--events", action="store_true", help="list every event")


def run(args) -> int:
    c = conn()
    r = store.run(c, args.run)
    if r is None:
        raise KeyError(f"no run {args.run}")
    for key in ("id", "thread_id", "state", "provider", "row", "why", "priority", "attempt",
                "parent", "error"):
        if r[key] not in (None, ""):
            print(f"{key:<10} {r[key]}")
    print(f"{'created':<10} {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))}")
    print(f"{'prompt':<10} {r['prompt']}")
    if args.events:
        for ev in store.events_after(c, r["id"]):
            print(f"  [{ev['attempt']}] {ev['kind']:<11} {json.loads(ev['data'])}")
    said = store.answer(c, r["id"])
    if said:
        print("\n" + said)
    return 0
