# SPDX-License-Identifier: Apache-2.0
"""`eki image|write|submit|wait|capabilities`: the commands for an agent with a
shell — blocking, quiet, a path out, real exit codes (eki/produce.py does the work)."""
from __future__ import annotations

from .common import ensure_engine

ORDER = 20


def register(sub) -> None:
    for name, helptext in (("image", "make a picture, save it, print its path (for agents)"),
                           ("write", "have a model write something, save it, print its path (for agents)")):
        c = sub.add_parser(name, help=helptext)
        c.add_argument("prompt")
        c.add_argument("-m", "--model", default="", help="a backend by key (see `eki backends`); default routed")
        c.add_argument("-o", "--output", default="",
                       help="a file or folder (default: here)" + ("; - prints the text" if name == "write" else ""))
        c.add_argument("--json", action="store_true", help="one JSON object: ok, paths, run, backend, error")
        c.add_argument("--timeout", type=float, default=900, help="seconds to wait (exit 5 after)")
        if name == "image":
            c.add_argument("--width", type=int, default=0)
            c.add_argument("--height", type=int, default=0)
            c.add_argument("-n", "--count", type=int, default=0, help="how many (1-4)")
        c.set_defaults(func=cmd_produce)

    su = sub.add_parser("submit", help="start something slow, print its run id, return (for agents)")
    su.add_argument("prompt")
    su.add_argument("-m", "--model", default="", help="a backend by key (see `eki backends`); default routed")
    su.add_argument("-r", "--repo", help="a folder it may edit")
    su.add_argument("--image", action="store_true", help="the answer is a picture")
    su.add_argument("--read-only", action="store_true", help="the run may read, not edit (a review)")
    su.add_argument("--allow", action="append", metavar="CMD", help="a command the run may use (repeatable)")
    su.add_argument("--write", action="append", metavar="PATH",
                    help="a path the run may write besides its copy (repeatable)")
    su.add_argument("--json", action="store_true", help="one JSON object: ok, run, conversation, error")
    su.set_defaults(func=cmd_produce)
    wt = sub.add_parser("wait", help="wait for a submitted run; print what it made (for agents)")
    wt.add_argument("id")
    wt.add_argument("-o", "--output", default="",
                    help="pictures: a folder or file (default: here); text: a file (default: printed)")
    wt.add_argument("--json", action="store_true", help="one JSON object: ok, paths, text, run, backend, error")
    wt.add_argument("--timeout", type=float, default=3600, help="seconds to wait (exit 5 after; it keeps running)")
    wt.set_defaults(func=cmd_produce)

    cp = sub.add_parser("capabilities", help="what this Mac can do right now, and how to reach it (for agents)")
    cp.add_argument("--json", action="store_true",
                    help="one JSON object: ok, depth, max_depth, can_ask, backends, error")
    cp.set_defaults(func=cmd_produce)


def cmd_produce(args) -> int:
    from .. import produce
    return getattr(produce, args.cmd)(args, ensure_engine)
