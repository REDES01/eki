# SPDX-License-Identifier: Apache-2.0
"""`eki observe`: what eki noticed about itself, and the fixes it proposed."""
from __future__ import annotations

import time

ORDER = 160


def register(sub) -> None:
    ob = sub.add_parser("observe", help="what eki noticed about itself, and the fixes it proposed")
    ob.add_argument("--days", type=float, default=7)
    ob.add_argument("--all", action="store_true", help="also the latest entries of every kind")
    ob.set_defaults(func=cmd_observe)


def cmd_observe(args) -> int:
    """What eki noticed about itself, and the fixes it proposed (eki/observe.py)."""
    from .. import observe
    data = observe.summary(args.days)
    kinds = data["kinds"]
    if not kinds:
        print(f"nothing noticed in the last {args.days:g} days")
        return 0
    print(f"last {args.days:g} days: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    if data["faults"]:
        print("\nfaults in eki's own code:")
        for f in data["faults"]:
            p = f.get("proposal") or {}
            state = p.get("state") or "—"
            extra = f"  {p['branch']}" if p.get("branch") else ""
            print(f"  ×{f['count']:<3} {f['signature']}\n        {f.get('error', '')[:90]}"
                  f"\n        fix: {state}{extra}" + (f" — {p['verdict'][:80]}" if p.get("verdict") else ""))
    if args.all:
        print("\nlatest:")
        for e in data["recent"]:
            when = time.strftime("%m-%d %H:%M", time.localtime(e["at"]))
            what = e.get("signal") or e.get("what") or e.get("signature") or e.get("error") or ""
            print(f"  {when}  {e['kind']:<8} {str(what)[:60]:<60} {e.get('backend', '')}")
    return 0
