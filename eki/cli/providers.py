"""The providers on this Mac and whether each can take work now."""
from __future__ import annotations

from .. import capacity, paths, providers
from .common import conn

NAME = "providers"
HELP = "list providers and whether each can take work now"


def add(p) -> None:
    pass


def run(args) -> int:
    avail = capacity.status(conn())
    print(f"({paths.config('providers')})")
    for name, cfg in providers.config().items():
        ok, why = avail.get(name, (False, "?"))
        print(f"{'✓' if ok else '✗'} {name:<8} {cfg.get('kind', ''):<12} {cfg.get('label', '')}"
              + ("" if ok else f"  — {why}"))
    return 0
