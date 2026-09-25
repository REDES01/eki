"""Local models: which are up, and starting or stopping them."""
from __future__ import annotations

from .. import models

NAME = "models"
HELP = "list local models; start or stop one"


def add(p) -> None:
    p.add_argument("action", choices=["list", "start", "stop"], nargs="?", default="list")
    p.add_argument("name", nargs="?")


def run(args) -> int:
    if args.action in ("start", "stop"):
        names = [args.name] if args.name else list(models.local_models())
        for n in names:
            st = models.start(n) if args.action == "start" else models.stop(n)
            print(f"{n}: {'up' if st['up'] else 'starting' if st['starting'] else 'down'}")
        return 0
    for n in models.local_models():
        st = models.status(n)
        state = "up" if st["up"] else "starting" if st["starting"] else "down"
        flags = [f for f, on in (("eki runs it", st["managed"]), ("kept up", st["keep_up"]),
                                 ("held by you", st["held"]), ("can start", st["startable"])) if on]
        print(f"{n:<10} {state:<9} {', '.join(flags)}" + ("" if st["up"] else f"  — {st['why']}"))
    return 0
