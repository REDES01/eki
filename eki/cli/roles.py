"""`eki roles` — eki's own jobs (router, planner, reviewer, chores) and who does each.

With no arguments it lists each role: its target, the model that resolves
to, whether it's the default, and why when the pick fell back. `eki roles
ROLE TARGET` sets one in routing.json (every other key kept); `eki roles
ROLE default` removes the entry so the role reads the old settings again.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from .. import providers, roles
from .common import conn, err

NAME = "roles"
HELP = "eki's own jobs (router, planner, reviewer, chores): show them, or set one to a target"


def add(p) -> None:
    p.add_argument("role", nargs="?", help=f"one of {', '.join(roles.ROLES)}")
    p.add_argument("target", nargs="?",
                   help="provider, provider:tier or provider:model; 'default' clears it, 'off' "
                        "switches the reviewer or chores off")


def listed(c: sqlite3.Connection, name: str) -> Optional[List[str]]:
    """The provider's read model list, or None when it was never read."""
    got = [r[0] for r in c.execute("SELECT model FROM provider_models WHERE provider=?", (name,))]
    return got or None


def _secs(ms: float) -> str:
    return f"{ms / 1000:.1f} s"


def line(c: sqlite3.Connection, role: str, *, timed: bool = False) -> str:
    """`router    claude:fast → claude-haiku-4-5   median 2.4 s over 20 (local: …)`."""
    want = roles.get(role)
    p = roles.pick(role, c)
    out = f"{role:<9} {want or '(none)'}"
    if p.provider is None:
        out += " → nobody" if want != "off" else ""
    else:
        if p.target != want:
            out += f" → {p.target}"
        out += f" → {p.model or 'its default'}"
    if timed:
        spent = roles.timings(c, role)
        mine = spent.pop(p.target, None) if p.provider else None
        if mine:
            out += f"   median {_secs(mine[0])} over {mine[1]}"
        if spent:
            others = ", ".join(f"{t}: {_secs(ms)} over {n}" for t, (ms, n) in spent.items())
            out += f" ({others})" if mine else f"   ({others})"
    if p.why and p.why != "off":
        out += f"   ({p.why})"
    return out


def show(c: sqlite3.Connection) -> int:
    for role in roles.ROLES:
        mark = "  [default]" if roles.get(role) == roles.default(role) else ""
        print(line(c, role) + mark)
    return 0


def run(args) -> int:
    c = conn()
    if args.role is None:
        return show(c)
    if args.role not in roles.ROLES:
        err(f"eki roles: unknown role {args.role!r} (the roles are {', '.join(roles.ROLES)})")
        return 1
    if args.target is None:
        print(line(c, args.role, timed=True))
        return 0
    target = args.target.strip()
    if target == "default":
        roles.set_role(args.role, None)
        print(f"{args.role}: back to its default, {roles.default(args.role) or '(none)'}")
        return 0
    # roles.problems checks the role and "off", and the target through targets.problems
    bad = roles.problems({args.role: target}, set(providers.config()))
    if bad:
        for b in bad:
            err(f"eki roles: {b}")
        return 1
    roles.set_role(args.role, target)
    print(line(c, args.role))
    return 0
