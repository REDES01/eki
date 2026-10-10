"""The routing table, and what would happen to a request — checkable on its own."""
from __future__ import annotations

from typing import Any, Dict

from .. import capacity, modellist, paths, providers, roles, routing, targets
from ..routing import needs
from ..routing.table import rows
from .common import conn
from .roles import line as role_line

NAME = "route"
HELP = "show the routing table, or explain where a request would go"


def add(p) -> None:
    p.add_argument("prompt", nargs="*", help="explain this request (leave out to show the table)")
    p.add_argument("-C", "--cwd", help="explain it as if asked in this folder")
    p.add_argument("--image", action="append", metavar="PATH",
                   help="explain it with this picture attached (repeat for more)")


def target_line(t: Dict[str, Any]) -> str:
    """✓/✗, the name, its can tags, and why when not ok (or when it starts on demand)."""
    line = f"  {'✓' if t['ok'] else '✗'} {t['name']} [{', '.join(t['can'])}]"
    return line + (f"  ({t['why']})" if t["why"] else "")


def run(args) -> int:
    c = conn()
    if args.prompt:
        e = routing.explain(c, " ".join(args.prompt), cwd=args.cwd, attachments=args.image)
        print(f"why:   {e['why']}")
        print(f"row:   {e['row']} — {e['title']}   needs: [{', '.join(e['needs'])}]")
        for t in e["targets"]:
            print(target_line(t))
        return 0
    avail = capacity.status(c)
    print(f"table: {paths.config('routing')}\n")
    for role in roles.ROLES:
        print(role_line(c, role, timed=role == "router"))
    print()
    for r in rows():
        print(f"{r['key']:<8} {r['title']}   needs: [{', '.join(needs.row_needs(r))}]")
        for t in r["targets"]:
            ok, why = avail.get(targets.provider_of(t), (False, "no such provider"))
            print(f"    {'✓' if ok else '✗'} {needs.describe(t)}{_model(c, t)}"
                  + (f"  ({why})" if why else ""))
    return 0


def _model(c, t: Any) -> str:
    """` → claude · Opus 5.5 · high` for a target naming a tier, model or
    effort, as it resolves against the read list (with the note when it falls
    back), else ""."""
    try:
        parsed = targets.parse(t)
    except ValueError as e:
        return f"  ({e})"
    if not (parsed.tier or parsed.model or parsed.effort):
        return ""
    name = parsed.provider
    listed = modellist.listed(c, name)
    got = targets.choose(parsed, providers.config().get(name) or {}, listed)
    return f" → {targets.label(name, got.model, got.effort, listed)}" + (f" ({got.note})" if got.note else "")
