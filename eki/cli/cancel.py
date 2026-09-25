"""Stop a run. Only you do this — eki itself never kills work."""
from __future__ import annotations

from .. import store
from .common import conn

NAME = "cancel"
HELP = "stop a run"


def add(p) -> None:
    p.add_argument("run")


def run(args) -> int:
    print(store.cancel(conn(), args.run))
    return 0
