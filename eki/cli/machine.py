"""`eki machine` — how much eki runs at once on this Mac: show the caps, or set them."""
from __future__ import annotations

from .. import machinecaps

NAME = "machine"
HELP = "how much eki runs at once on this Mac: agents at once and test runs at once"


def add(p) -> None:
    p.add_argument("--agents", type=int, metavar="N",
                   help=f"items building at once across all projects (1–{machinecaps.MOST})")
    p.add_argument("--checks", type=int, metavar="M",
                   help=f"test runs (checks, judges, agents' own tests) at once (1–{machinecaps.MOST})")


def run(args) -> int:
    if args.agents is not None or args.checks is not None:
        s = machinecaps.save(agents=args.agents, checks=args.checks)   # ValueError → `eki: ...`, exit 1
    else:
        s = machinecaps.settings()
    print(f"agents at once {s['agents']} (1–{machinecaps.MOST}) · test runs at once {s['checks']}")
    return 0
