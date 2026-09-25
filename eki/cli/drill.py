"""Kill the engine and a worker mid-run, in a throwaway home, and check nothing is lost."""
from __future__ import annotations

from .. import drill

NAME = "drill"
HELP = "prove a restart loses nothing (runs in a throwaway home)"


def add(p) -> None:
    pass


def run(args) -> int:
    print("restart drill:")
    try:
        drill.run()
    except AssertionError as e:
        print(f"  ✗ {e}")
        return 1
    print("passed")
    return 0
