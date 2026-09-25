"""Follow a run: what it has said so far, then live until it ends."""
from __future__ import annotations

from .. import store
from .common import conn, err, follow

NAME = "follow"
HELP = "show a run's output, live until it ends"


def add(p) -> None:
    p.add_argument("run", nargs="?", help="run id (default: the latest)")
    p.add_argument("-q", "--quiet", action="store_true", help="no tool lines")


def run(args) -> int:
    c = conn()
    r = store.run(c, args.run) if args.run else (store.recent_runs(c, 1) or [None])[0]
    if r is None:
        raise KeyError("no such run")
    try:
        return follow(c, r["id"], show_tools=not args.quiet)
    except KeyboardInterrupt:
        err("\n(stopped following; the run carries on)")
        return 130
