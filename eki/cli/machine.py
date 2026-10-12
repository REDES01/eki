"""`eki machine` — how much eki runs at once on this Mac: show the caps and the memory floor, or set them."""
from __future__ import annotations

from .. import db, machinecaps, memadmit

NAME = "machine"
HELP = "how much eki runs at once on this Mac: agents, test runs, browsers and the memory floor"
KEYS = ("agents", "checks", "browsers", "floor_gb", "swap_max")


def add(p) -> None:
    p.add_argument("--agents", type=int, metavar="N",
                   help=f"items building at once across all projects (1–{machinecaps.MOST['agents']})")
    p.add_argument("--checks", type=int, metavar="M",
                   help=f"test runs (checks, judges, agents' own tests) at once (1–{machinecaps.MOST['checks']})")
    p.add_argument("--browsers", type=int, metavar="B",
                   help=f"browsers open at once across all runs (1–{machinecaps.MOST['browsers']})")
    p.add_argument("--floor-gb", type=int, metavar="N", dest="floor_gb",
                   help=f"new work waits while free memory is below N GB (1–{machinecaps.MOST['floor_gb']})")
    p.add_argument("--swap-max", type=int, metavar="P", dest="swap_max",
                   help=f"new work waits while more than P% of swap is in use (1–{machinecaps.MOST['swap_max']})")


def _estimates() -> str:
    conn = db.connect()
    try:
        got = memadmit.estimates(conn)
    finally:
        conn.close()
    return "estimates: " + " · ".join(f"{e['kind']} {e['gb']:.1f} GB" for e in got)


def run(args) -> int:
    given = {k: getattr(args, k) for k in KEYS if getattr(args, k) is not None}
    # ValueError → `eki: ...`, exit 1
    s = machinecaps.save(**given) if given else machinecaps.settings()
    print(f"agents at once {s['agents']} (1–{machinecaps.MOST['agents']}) · test runs at once {s['checks']}"
          f" · browsers at once {s['browsers']} · memory floor {s['floor_gb']} GB · swap at most {s['swap_max']}%")
    print(_estimates())
    return 0
