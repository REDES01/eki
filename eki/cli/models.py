# SPDX-License-Identifier: Apache-2.0
"""`eki models`: local weights — what's loaded, start, stop."""
from __future__ import annotations

import asyncio
import sys

from .. import config as config_mod

ORDER = 90


def register(sub) -> None:
    m = sub.add_parser("models", help="local weights: what's loaded, start, stop")
    m.add_argument("action", nargs="?", default="list", choices=["list", "start", "stop"])
    m.add_argument("key", nargs="?", default="")
    m.add_argument("-f", "--force", action="store_true",
                   help="start it even if the memory ceiling says no")
    m.set_defaults(func=lambda args: cmd_models(config_mod.load(args.config), args))


def cmd_models(cfg, args) -> int:
    """Local weights, without needing the service up."""
    from ..models import ModelManager
    manager = ModelManager(cfg.local_models, cfg.memory_ceiling_gb)

    if args.action in ("start", "stop"):
        if manager.get(args.key) is None:
            print(f"no such model: {args.key}", file=sys.stderr)
            return 1
        coro = (manager.start(args.key, force=args.force) if args.action == "start"
                else manager.stop(args.key))
        print(asyncio.run(coro))
        return 0

    memory = manager.memory()
    for m in manager.describe():
        mark = "up  " if m["running"] else ("--  " if m["can_start"] else "    ")
        blocked = "  (no room)" if m["blocked_by_memory"] else ""
        print(f"{mark} {m['key']:8} :{m['port']:<6} {m['gb']:>5.1f}GB  "
              f"{m['label']}{blocked}")
    print(f"\n{memory.committed_gb}GB loaded · {memory.free_gb}GB free "
          f"of a {memory.ceiling_gb}GB ceiling ({memory.total_gb}GB installed)")
    return 0
