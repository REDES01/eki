"""`eki roles` — eki's own jobs (router, planner, reviewer, chores) and who does each.

With no arguments it lists each role: its target, what that comes to
(`claude · Haiku 4.5 · low`), whether it's the default, and why when the
pick fell back. `eki roles ROLE PROVIDER [MODEL] [--effort LEVEL]` sets one
in routing.json (every other key kept), written in targets.fmt()'s shortest
form; `provider:tier` still works. When the provider's model list was read,
a model it doesn't list or an effort the model doesn't offer is refused with
what is offered. `eki roles ROLE default` removes the entry so the role
reads the old settings again.
"""
from __future__ import annotations

import sqlite3
from typing import Any, List, Optional

from .. import modellist, providers, roles, targets
from .common import conn, err

NAME = "roles"
HELP = "eki's own jobs (router, planner, reviewer, chores): show them, or set one to a target"


def add(p) -> None:
    p.add_argument("role", nargs="?", help=f"one of {', '.join(roles.ROLES)}")
    p.add_argument("target", nargs="?",
                   help="provider (or provider:tier, provider:model); 'default' clears it, 'off' "
                        "switches the reviewer or chores off")
    p.add_argument("model", nargs="?", help="the model, by its id or alias (leave out for its default)")
    p.add_argument("--effort", metavar="LEVEL", help="the effort, one the model offers")


def _secs(ms: float) -> str:
    return f"{ms / 1000:.1f} s"


def line(c: sqlite3.Connection, role: str, *, timed: bool = False) -> str:
    """`router    claude:haiku → claude · Haiku 4.5 · low   median 2.4 s over 20 (local: …)`."""
    want = roles.shown(roles.get(role))
    p = roles.pick(role, c)
    out = f"{role:<9} {want or '(none)'}"
    if p.provider is None:
        out += " → nobody" if want != "off" else ""
    else:
        if roles.shown(p.target) != want:
            out += f" → {roles.shown(p.target)}"
        out += f" → {targets.label(p.provider, p.model, p.effort, modellist.listed(c, p.provider))}"
    if timed:
        spent = roles.timings(c, role)
        mine = spent.pop(roles.shown(p.target), None) if p.provider else None
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
        mark = "  [default]" if roles.shown(roles.get(role)) == roles.shown(roles.default(role)) else ""
        print(line(c, role) + mark)
    return 0


def _written(args) -> Any:
    """The target as the arguments name it, or raise ValueError."""
    target = args.target.strip()
    if args.model is None and args.effort is None:
        return target
    if ":" in target:
        raise ValueError(f"name the model either as {target} or as a separate word, not both")
    out = {"provider": target}
    if args.model is not None:
        out["model"] = args.model.strip()
    if args.effort is not None:
        out["effort"] = args.effort.strip()
    return out


def _offered(efforts: List[str], default: Optional[str]) -> str:
    return ", ".join(f"{e} (default)" if e == default else e for e in efforts)


def unoffered(c: sqlite3.Connection, target: Any) -> List[str]:
    """What the provider's read list doesn't offer of `target`, with what it
    does ([] when all is offered, or the list was never read)."""
    t = targets.parse(target)
    listed = modellist.listed(c, t.provider)
    if not listed:
        return []
    model = t.model
    if t.tier:                       # a tier reads through providers.json, as choose() does
        model = targets.choose(targets.Target(t.provider, tier=t.tier),
                               providers.config().get(t.provider) or {}, None).model
    ids = [r["model"] for r in listed]
    if model is not None and model not in ids:
        return [f"{t.provider} doesn't list {model}; it offers {', '.join(ids)}"]
    if not t.effort:
        return []
    row = next((r for r in listed if (r["model"] == model if model else r["is_default"])), None)
    if row is None:
        return []
    name = row["model"] if model is None else model
    if row["efforts"] is None:
        return [f"{t.provider} {name} takes no effort"]
    if t.effort not in row["efforts"]:
        return [f"{t.provider} {name} doesn't offer effort {t.effort}; "
                f"it offers {_offered(row['efforts'], row['default_effort'])}"]
    return []


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
    try:
        target = _written(args)
    except ValueError as e:
        err(f"eki roles: {e}")
        return 1
    if target == "default":
        roles.set_role(args.role, None)
        print(f"{args.role}: back to its default, {roles.shown(roles.default(args.role)) or '(none)'}")
        return 0
    # roles.problems checks the role and "off", and the target through targets.problems
    bad = roles.problems({args.role: target}, set(providers.config()))
    if not bad and target != "off":
        bad = unoffered(c, target)
    if bad:
        for b in bad:
            err(f"eki roles: {b}")
        return 1
    roles.set_role(args.role, target)
    print(line(c, args.role))
    return 0
