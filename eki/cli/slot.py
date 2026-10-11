"""`eki slot -- <cmd...>` — run a test command inside one of the machine's test slots."""
from __future__ import annotations

import argparse
import subprocess

from .. import testslots
from .common import conn, err

NAME = "slot"
HELP = "run a test command (bin/check, npm test, pytest) in one of the machine's test slots"


def add(p) -> None:
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="the command, after --")


def run(args) -> int:
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        err("usage: eki slot -- <cmd...>")
        return 2
    c = conn()
    try:
        with testslots.hold(c):
            return subprocess.call(cmd)     # stdin, stdout, stderr: the caller's own
    finally:
        c.close()
