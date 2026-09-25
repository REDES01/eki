# SPDX-License-Identifier: Apache-2.0
"""`eki builds`: what the engine runs, what it ran before, the last swap."""
from __future__ import annotations

import time

from .common import _going_live

ORDER = 170


def register(sub) -> None:
    sub.add_parser("builds", help="what the engine runs, what it ran before, the last swap").set_defaults(
        func=cmd_builds)


def cmd_builds(args) -> int:
    """What the engine runs, what it ran before, and how the last swap went."""
    from .. import builds
    rows = builds.listing()
    for r in rows:
        mark = "→" if r["current"] else ("↩" if r["previous"] else " ")
        what = "your checkout" if r.get("dev") else (r.get("note") or r.get("ref") or "")
        print(f"{mark} {r['id']:<11} {(r.get('commit') or '')[:10]:<11} {what:<24} {r['path']}")
    if not rows:
        print("no builds — the engine runs from your checkout (`eki agent install` sets this up)")
    s = builds.last_swap()
    if s:
        when = time.strftime("%m-%d %H:%M", time.localtime(s.get("at") or 0))
        print(f"\nlast swap {when}: {s.get('state')} — {s.get('target')}"
              + (f" ({s['why']})" if s.get("why") else ""))
    else:
        print("\nno swap yet")
    going = _going_live(builds.going_live())
    if going:
        print(going)
    from .. import appbuild
    line = appbuild.versions_line()
    if line:
        print(line)
    return 0
