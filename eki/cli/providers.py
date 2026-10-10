"""The providers on this Mac and whether each can take work now; their model lists."""
from __future__ import annotations

import sys
from typing import Any, Dict

from .. import capacity, modellist, paths, providers
from .common import ago, conn

NAME = "providers"
HELP = "list providers; `models [name] [--refresh]` what each program says it offers"


def add(p) -> None:
    p.add_argument("action", nargs="?", choices=["list", "models"], default="list")
    p.add_argument("rest", nargs="*", help="models: [name]")
    p.add_argument("--refresh", action="store_true", help="with models: read the lists again first")


def run(args) -> int:
    if args.action == "models":
        return _models(args)
    avail = capacity.status(conn())
    print(f"({paths.config('providers')})")
    for name, cfg in providers.config().items():
        if name in providers.BUILTIN:
            continue
        ok, why = avail.get(name, (False, "?"))
        can = "[" + ", ".join(providers.capabilities(name, cfg)) + "]"
        print(f"{'✓' if ok else '✗'} {name:<8} {cfg.get('kind', ''):<12} {cfg.get('label', '')}"
              f"  {can}" + ("" if ok else f"  — {why}"))
        if cfg.get("about"):
            print(f"    {cfg['about']}")
    return 0


def _models(args) -> int:
    c = conn()
    want = args.rest[0] if args.rest else None
    every = providers.config()
    if want is not None and want not in every:
        print(f"eki: no provider named {want!r}", file=sys.stderr)
        return 1
    names = [n for n, cfg in every.items() if (n == want if want else
                                                cfg.get("kind") in modellist.KINDS and not cfg.get("off"))]
    code = 0
    for name in names:
        cfg = every[name]
        if args.refresh:
            try:
                modellist.refresh(c, name)
            except Exception as e:                       # noqa: BLE001 — say it, go on to the next
                print(f"✗ {name}: couldn't read its list: {e}", file=sys.stderr)
                code = 1
        got = modellist.listed(c, name)
        print(f"{name} ({cfg.get('kind', '')}) — " + (f"read {ago(modellist.read_at(c, name))}"
                                                     if got is not None else "list never read"))
        for m in got or []:
            for line in _model(m):
                print(line)
    return code


def _model(m: Dict[str, Any]) -> list:
    """One model as the program put it: id and name, what it says of it, its efforts."""
    head = f"  {m['model']}" + (f"  {m['label']}" if m["label"] != m["model"] else "")
    if m["is_default"]:
        head += "  (its default)"
    if m["floating"]:
        head += "  follows the newest" + (f" (now {m['resolved']})" if m.get("resolved") not in (None, m["model"]) else "")
    out = [head]
    if m.get("description"):
        out.append(f"      {m['description']}")
    if m.get("efforts"):
        out.append("      effort: " + ", ".join(e + (" (default)" if e == m.get("default_effort") else "")
                                         for e in m["efforts"]))
    return out
