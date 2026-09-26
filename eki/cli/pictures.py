"""Pictures: everything eki has drawn, newest first."""
from __future__ import annotations

from .. import gallery
from .common import conn

NAME = "pictures"
HELP = "list the pictures eki has drawn"


def add(p) -> None:
    p.add_argument("--limit", type=int, default=20)


def run(args) -> int:
    got = gallery.pictures(conn(), max(1, args.limit))
    if not got:
        print("no pictures yet")
    for pic in got:
        prompt = " ".join((pic["prompt"] or "").split())
        print(f"{pic['path']}  {pic['row'] or '-'}  {prompt[:60]}")
    return 0
