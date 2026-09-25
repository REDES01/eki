# SPDX-License-Identifier: Apache-2.0
"""`eki lineup`: which models are out there — vendor ladders, local suggestions."""
from __future__ import annotations

import json
import subprocess
import sys
import time

import httpx

from .common import call

ORDER = 150


def register(sub) -> None:
    wa = sub.add_parser("lineup", help="which models are out there: vendor ladders, local suggestions")
    wa.add_argument("action", nargs="?", default="show", choices=["show", "refresh", "take", "update"])
    wa.add_argument("name", nargs="?", default="")
    wa.set_defaults(func=cmd_watch)


def cmd_watch(args) -> int:
    """Which models are out there: the vendors' ladders, local suggestions."""
    if args.action == "refresh":
        print("reading the vendors' pages and Ollama's popular models… (a minute or two)",
              file=sys.stderr)
        httpx_timeout = 900
        try:
            data = httpx.post(f"{args.service}/api/watch/refresh", timeout=httpx_timeout).json()
        except httpx.HTTPError as e:
            print(f"! {e}", file=sys.stderr)
            return 1
    elif args.action == "update":
        from .. import watch as watch_mod
        data = call("GET", "/api/watch", args.service)
        todo = [(k, v.get("program") or {}) for k, v in (data.get("vendors") or {}).items()
                if (v.get("program") or {}).get("behind") and (not args.name or args.name in k)]
        if not todo:
            print("the programs are up to date (as of the last reading)")
            return 0
        failed = False
        for key, prog in todo:
            print(f"updating {key} {prog.get('installed')} → {prog.get('latest')}: {' '.join(prog['update'])}")
            if subprocess.run(prog["update"]).returncode != 0:
                print(f"! {key} didn't update — see above", file=sys.stderr)
                failed = True
        if failed:
            return 1
        print("done — `eki lineup refresh` to read again; open threads pick the new version up "
              "when their session next starts")
        return 0
    elif args.action == "take":
        got = call("POST", f"/api/watch/take/{args.name}", args.service)
        print(f"setting up {args.name}: run {got.get('run')} — follow it with `eki watch {got.get('run')}`"
              if got.get("run") else json.dumps(got))
        return 0
    else:
        data = call("GET", "/api/watch", args.service)
    if not data.get("at"):
        print("not read yet — `eki lineup refresh`")
        return 0
    when = time.strftime("%m-%d %H:%M", time.localtime(data["at"]))
    print(f"read {when}")
    for prov, v in (data.get("vendors") or {}).items():
        lad = v.get("ladder") or {}
        roles = " · ".join(f"{r}: {lad[r] or '(its default)'}" for r in ("default", "top", "fast") if r in lad)
        print(f"\n{prov} — {v.get('vendor')}: {roles}")
        if v.get("guidance"):
            print(f"  “{v['guidance'][:220]}”")
        prog = v.get("program") or {}
        for st in prog.get("stale") or []:
            print(f"  ! its {st['role']} is {st['vendor']}, but {st['alias']!r} here runs {st['runs']}")
        if prog.get("behind"):
            print(f"  ! {prog.get('installed')} installed, {prog['latest']} is out — `eki lineup update`")
        if v.get("not_offered"):
            print(f"  listed by {v.get('vendor')} but not runnable here yet: {', '.join(v['not_offered'][:5])}")
    sugg = data.get("suggestions") or []
    print("\nlocal:" if sugg else "\nlocal: nothing better than what you run, today")
    for s in sugg:
        build = s.get("build") or "no MLX build yet"
        print(f"  {s['name']:<22} {s['why']}\n  {'':<22} → {build}   (eki lineup take {s['name']})")
    for e in data.get("errors") or []:
        print(f"  ! {e}")
    return 0
