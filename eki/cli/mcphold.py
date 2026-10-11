"""`eki mcp-hold --run <run> --dir <folder> <card> -- <command> <args…>` — an MCP server that holds its card.

A stdio proxy: the server runs behind it, and its first tool call waits
until <card> is held for <run> (eki/components/mcphold.py).
"""
from __future__ import annotations

import argparse

from ..components import mcphold

NAME = "mcp-hold"
HELP = "run an MCP server behind a proxy that holds its card before the first tool call"


def add(p) -> None:
    p.add_argument("--run", default="", help="the agent's run the card is held for (empty: just relay)")
    p.add_argument("--dir", default="", help="the caller's folder")
    p.add_argument("card", help="the card to hold")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- the server command and its arguments")


def run(args) -> int:
    argv = list(args.command)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return mcphold.main(argv, card=args.card, run_id=args.run, folder=args.dir)
