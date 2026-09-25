# SPDX-License-Identifier: Apache-2.0
"""`eki ask`: tell it to do something; the answer streams here."""
from __future__ import annotations

import os

from .. import config as config_mod
from ..store import Store
from .common import call, watch

ORDER = 10


def register(sub) -> None:
    a = sub.add_parser("ask", help="tell it to do something")
    a.add_argument("prompt")
    a.add_argument("--backend", help="force a backend by key")
    a.add_argument("-r", "--repo", help="a folder it may edit")
    a.add_argument("--image", action="store_true",
                   help="the answer is a picture — only image backends apply")
    a.add_argument("--read-only", action="store_true",
                   help="from an agent: the run may read, not edit (a review)")
    a.add_argument("--allow", action="append", metavar="CMD",
                   help="from an agent: a command the run may use, e.g. 'pytest' (repeatable)")
    a.add_argument("--write", action="append", metavar="PATH",
                   help="from an agent: a path the run may write besides its copy (repeatable)")
    a.add_argument("--continue", dest="continue_", action="store_true",
                   help="continue the most recent conversation")
    a.add_argument("-d", "--detach", action="store_true",
                   help="start it and print the run id instead of watching")
    a.add_argument("-q", "--quiet", action="store_true", help="answer only, at the end")
    a.set_defaults(func=lambda args: cmd_ask(config_mod.load(args.config), args))


def cmd_ask(cfg, args) -> int:
    conversation = ""
    if args.continue_:
        conversation = Store(cfg.db).latest_conversation() or ""
    body = {"prompt": args.prompt, "conversation": conversation,
            "backend": args.backend or "",
            # the engine's folder isn't yours: `--repo .` means here
            "repo": os.path.abspath(os.path.expanduser(args.repo)) if args.repo else "",
            "images": bool(args.image)}
    # run by a program the engine started (a goal's agent testing eki, say):
    # its request, not yours, one level down from the run asking
    from .. import produce
    produce.from_agent(produce.located(body), read_only=bool(getattr(args, "read_only", False)),
                       commands=getattr(args, "allow", None), paths=getattr(args, "write", None))
    started = call("POST", "/api/ask", args.service, json=body)
    if args.detach:
        print(started["run"])
        return 0
    return watch(started["run"], args.service, quiet=args.quiet,
                 conversation=started["conversation"])
