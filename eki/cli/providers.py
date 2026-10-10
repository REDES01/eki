"""The providers on this Mac and whether each can take work now; their model lists and tiers."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

from .. import capacity, modellist, paths, providers, targets
from .common import ago, conn

NAME = "providers"
HELP = "list providers; `models [name] [--refresh]` their model lists; `tier <name> <tier> <model|suggested>`"


def add(p) -> None:
    p.add_argument("action", nargs="?", choices=["list", "models", "tier"], default="list")
    p.add_argument("rest", nargs="*", help="models: [name]; tier: <name> <tier> <model|suggested>")
    p.add_argument("--refresh", action="store_true", help="with models: read the lists again first")


def run(args) -> int:
    if args.action == "models":
        return _models(args)
    if args.action == "tier":
        return _tier(args.rest)
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


def picked(cfg: Dict[str, Any]) -> Dict[str, str]:
    """The tiers picked in a providers.json entry (the old `model` as default)."""
    own = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    out = {t: str(own[t]) for t in targets.TIERS if own.get(t)}
    if "default" not in out and cfg.get("model"):
        out["default"] = str(cfg["model"])
    return out


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
        mine, guess = picked(cfg), modellist.suggest(name, cfg.get("kind", ""), got)
        for tier in targets.TIERS:
            line = f"  {tier:<8} {mine.get(tier) or '-'}"
            if guess.get(tier) and guess[tier] != mine.get(tier):
                line += f"   (suggested {guess[tier]})"
            print(line)
        for m in got or []:
            print(f"    {m['model']}" + (f"  {m['label']}" if m["label"] != m["model"] else "")
                  + ("  (its default)" if m["is_default"] else ""))
    return code


def _tier(rest) -> int:
    if len(rest) != 3:
        print("eki: usage: eki providers tier <name> <tier> <model|suggested>", file=sys.stderr)
        return 1
    name, tier, model = rest
    if tier not in targets.TIERS:
        print(f"eki: no tier named {tier!r}; the tiers are {', '.join(targets.TIERS)}", file=sys.stderr)
        return 1
    path = paths.config("providers")
    providers.config()                                   # writes the file when there is none yet
    data = json.loads(path.read_text())
    entry = data.get(name)
    if not isinstance(entry, dict):
        print(f"eki: {name!r} isn't an entry in {path}", file=sys.stderr)
        return 1
    got = modellist.listed(conn(), name)
    if got is None:
        print(f"eki: {name}'s model list was never read; run `eki providers models {name} --refresh`",
              file=sys.stderr)
        return 1
    if model == "suggested":
        model = modellist.suggest(name, entry.get("kind", ""), got).get(tier, "")
        if not model:
            print(f"eki: nothing suggested for {name}'s {tier} tier", file=sys.stderr)
            return 1
    if model not in [m["model"] for m in got]:
        print(f"eki: {model!r} isn't in {name}'s list ({', '.join(m['model'] for m in got)})", file=sys.stderr)
        return 1
    tiers = entry.get("models") if isinstance(entry.get("models"), dict) else {}
    entry["models"] = {**tiers, tier: model}
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    print(f"{name} {tier} → {model}")
    return 0
