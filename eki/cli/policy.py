# SPDX-License-Identifier: Apache-2.0
"""`eki policy`: routing preferences, edited in place."""
from __future__ import annotations

import argparse
import sys

import httpx

from .. import config as config_mod
from .common import engine_up, parent_headers

ORDER = 80


def register(sub) -> None:
    p = sub.add_parser("policy", help="routing preferences")
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "on", "off", "tier", "prefer", "ceiling"])
    p.add_argument("key", nargs="?", default="")
    p.add_argument("value", nargs="?", default=None)
    p.set_defaults(func=lambda args: cmd_policy(config_mod.load(args.config), args))


def cmd_policy(cfg, args) -> int:
    """Routing preferences, edited in place — no config file surgery."""
    from .. import policy as policy_mod
    current = policy_mod.load()
    known = {b.key for b in cfg.backends}

    if args.action == "show":
        print(f"disabled: {', '.join(current.disabled) or '(none)'}")
        print(f"tiers:    {current.tiers or '(as configured)'}")
        print(f"order:    {', '.join(current.order) or '(config order)'}")
        ceiling = ("(as configured)" if current.quota_ceiling is None
                   else current.quota_ceiling)
        print(f"ceiling:  {ceiling}")
        return 0

    if args.key and args.key not in known:
        print(f"no backend named {args.key!r} — known: {', '.join(sorted(known))}",
              file=sys.stderr)
        return 1

    if args.action == "off" and args.key not in current.disabled:
        current.disabled.append(args.key)
    elif args.action == "on":
        current.disabled = [k for k in current.disabled if k != args.key]
    elif args.action == "tier":
        if args.value is None:
            print("tier needs a number", file=sys.stderr)
            return 1
        current.tiers[args.key] = int(args.value)
    elif args.action == "prefer":
        current.order = [args.key] + [k for k in current.order if k != args.key]
    elif args.action == "ceiling":
        current.quota_ceiling = float(args.value) if args.value is not None else None

    path = policy_mod.save(current)
    print(f"saved {path}")
    # the running engine holds its own copy; tell it, if it's there
    if engine_up(args.service):
        httpx.put(f"{args.service}/api/policy", json=current.to_json(), timeout=5,
                  headers=parent_headers())
    return cmd_policy(cfg, argparse.Namespace(action="show", key="", value=None,
                                              service=args.service))
