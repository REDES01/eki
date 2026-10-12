"""`eki machine` — how much eki runs at once on this Mac: show the caps, or set them."""
from __future__ import annotations

from .. import machinecaps

NAME = "machine"
HELP = "how much eki runs at once on this Mac: agents, test runs and browsers at once"


def add(p) -> None:
    p.add_argument("--agents", type=int, metavar="N",
                   help=f"items building at once across all projects (1–{machinecaps.MOST['agents']})")
    p.add_argument("--checks", type=int, metavar="M",
                   help=f"test runs (checks, judges, agents' own tests) at once (1–{machinecaps.MOST['checks']})")
    p.add_argument("--browsers", type=int, metavar="B",
                   help=f"browsers open at once across all runs (1–{machinecaps.MOST['browsers']})")


def run(args) -> int:
    if args.agents is not None or args.checks is not None or args.browsers is not None:
        # ValueError → `eki: ...`, exit 1
        s = machinecaps.save(agents=args.agents, checks=args.checks, browsers=args.browsers)
    else:
        s = machinecaps.settings()
    print(f"agents at once {s['agents']} (1–{machinecaps.MOST['agents']}) · test runs at once {s['checks']}"
          f" · browsers at once {s['browsers']}")
    return 0
