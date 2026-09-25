"""`eki <command>` — one file per command in this package.

Each command module has `NAME`, `HELP`, `add(parser)` and `run(args) -> int`.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from typing import List, Optional

COMMANDS = ["ask", "answer", "follow", "runs", "show", "cancel", "threads", "engine", "route",
            "providers", "models", "skills", "mcp", "open", "drill", "swap", "builds"]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eki", description="The station on your Mac.")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    for name in COMMANDS:
        mod = importlib.import_module(f"eki.cli.{name}")
        sp = sub.add_parser(mod.NAME, help=mod.HELP, description=mod.HELP)
        mod.add(sp)
        sp.set_defaults(_run=mod.run)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    if not getattr(args, "_run", None):
        parser().print_help()
        return 0
    try:
        return int(args._run(args) or 0)
    except KeyboardInterrupt:
        return 130
    except (KeyError, ValueError) as e:
        print(f"eki: {e}", file=sys.stderr)
        return 1
