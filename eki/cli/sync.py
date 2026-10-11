"""`eki sync` — what each direction of the Claude-client sync last did; `--diff` the instructions drift.

Read-only: it shows `syncstate.status()` and never runs a direction (housekeeping does).
"""
from __future__ import annotations

from .. import syncstate
from .common import ago

NAME = "sync"
HELP = "the Claude-client sync: each direction's last run, counts and clashes"


def add(p) -> None:
    p.add_argument("--diff", action="store_true", help="show how ~/.claude/CLAUDE.md and AGENTS.md differ")


def diff_text() -> str:
    try:
        return (syncstate.root() / "instructions.diff").read_text()
    except OSError:
        return ""


def lines() -> list:
    state = syncstate.status()
    width = max(len(d) for d in syncstate.DIRECTIONS)
    out = []
    for d in syncstate.DIRECTIONS:
        s = state[d]
        clashes = [str(c) for c in s.get("clashes") or []]
        when = ago(s.get("at")) if s.get("at") else "never"
        said = f"{len(clashes)} clash{'es' if len(clashes) != 1 else ''}" if clashes else "no clashes"
        out.append(f"{d:<{width}}  {when:<8}  {int(s.get('count') or 0)}  {said}")
        out.extend(f"    {c}" for c in clashes)
    return out


def run(args) -> int:
    if args.diff:
        text = diff_text()
        print(text.rstrip("\n") if text.strip() else "no drift")
        return 0
    print("\n".join(lines()))
    return 0
