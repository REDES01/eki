# SPDX-License-Identifier: Apache-2.0
"""`eki goals`: things eki keeps doing when the machine has room (eki/goals.py)."""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, Optional

from .common import call

ORDER = 190


def register(sub) -> None:
    g = sub.add_parser("goals", help="things eki keeps doing when the machine has room")
    g.add_argument("action", nargs="?", default="show",
                   choices=["show", "add", "rm", "pause", "resume", "run", "mode", "report"])
    g.add_argument("arg", nargs="?", default="",
                   help="what to keep doing, in words (add); a goal id (rm, pause, resume, run); "
                        "on|off|resources|away (mode); hours (report)")
    g.add_argument("-f", "--folder", default="", help="add: a folder for it to work in")
    g.add_argument("--every", default="", help="add: day, weekdays, week, mon…sun, or like 4h (default: once, until done)")
    g.add_argument("--at", default="", help="add: the time of day, HH:MM (default 09:00)")
    g.add_argument("--spare", action="store_true",
                   help="add: may use your subscriptions, within their spare room")
    g.add_argument("--screen", action="store_true",
                   help="add: may look at and use the screen — its turns run only while you're away "
                        "(and it may use your subscriptions' spare room)")
    g.add_argument("--on-time", action="store_true",
                   help="add, repeating: run at the time, not when the machine has room")
    g.add_argument("--fresh", action="store_true",
                   help="add, repeating: each time in a new thread, not carrying on the last")
    g.set_defaults(func=cmd_goals)


def _when_of(every: str, at: str) -> Optional[Dict[str, Any]]:
    """--every day|weekdays|week|mon…sun|<n>h|<n>m, --at HH:MM → a goal's when."""
    if not every:
        return None
    every = every.lower().strip()
    at = at or "09:00"
    days = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    if every in ("day", "daily"):
        return {"kind": "daily", "at": at}
    if every in ("weekday", "weekdays"):
        return {"kind": "daily", "at": at, "days": [0, 1, 2, 3, 4]}
    if every in ("week", "weekly"):
        return {"kind": "daily", "at": at, "days": [0]}
    if every[:3] in days:
        return {"kind": "daily", "at": at, "days": [days[every[:3]]]}
    import re as _re
    m = _re.fullmatch(r"(\d+)\s*(h|m|hours?|min(utes?)?)", every)
    if m:
        n = int(m.group(1))
        return {"kind": "interval", "minutes": n * 60 if m.group(2).startswith("h") else n}
    raise SystemExit(f"--every is day, weekdays, week, a weekday (mon…sun), or like 4h / 90m — not {every!r}")


def _goal_line(g: Dict[str, Any]) -> str:
    folder = f" · {g['folder']}" if g.get("folder") else ""
    spare = " · may use subscriptions" if g.get("spare") else ""
    if g.get("screen"):
        spare = " · uses the screen when you're away, and subscriptions"
    if g.get("repeats") and g.get("on_time"):
        spare += " · on time"
    if g.get("repeats") and g.get("fresh"):
        spare += " · fresh each time"
    return f"{g['id'][:6]}  {g['status']:<10} {g['text'][:70]}\n        {g['when_text']}{folder}{spare}" + \
        (f"\n        {g['note']}" if g.get("note") else "")


def cmd_goals(args) -> int:
    """Goals: things eki keeps doing when the machine has room (eki/goals.py)."""
    a = args.action
    if a == "add":
        if not args.arg:
            print('eki goals add "every morning, look at my X and propose 3 posts" --every day --at 08:00',
                  file=sys.stderr)
            return 2
        folder = os.path.abspath(os.path.expanduser(args.folder)) if args.folder else ""
        g = call("POST", "/api/goals", args.service,
                 json={"text": args.arg, "when": _when_of(args.every, args.at), "folder": folder,
                       "spare": args.spare, "screen": args.screen, "on_time": args.on_time,
                       "fresh": args.fresh})
        print(f"added {g['id'][:6]} — {g['when_text']}; its first turn comes when the machine has room")
        return 0
    if a in ("rm", "pause", "resume", "run"):
        if not args.arg:
            print(f"eki goals {a} <goal id>", file=sys.stderr)
            return 2
        gid = next((g["id"] for g in call("GET", "/api/goals", args.service)["goals"]
                    if g["id"].startswith(args.arg)), args.arg)
        if a == "rm":
            got = call("DELETE", f"/api/goals/{gid}", args.service)
            print(f"removed {gid[:6]} (its thread stays in your history)")
        elif a == "run":
            call("POST", f"/api/goals/{gid}/run", args.service)
            print("its next turn comes as soon as there's room")
        else:
            call("PATCH", f"/api/goals/{gid}", args.service,
                 json={"state": "paused" if a == "pause" else "active"})
            print("paused" if a == "pause" else "resumed")
        return 0
    if a == "mode":
        body = {"on": True} if args.arg == "on" else {"on": False} if args.arg == "off" else \
            {"when": args.arg} if args.arg in ("resources", "away") else None
        if body is None:
            print("eki goals mode on|off          (background work at all)\n"
                  "eki goals mode resources|away  (whenever there's room, or only when you're away)",
                  file=sys.stderr)
            return 2
        call("POST", "/api/goals/mode", args.service, json=body)
    if a == "report":
        hours = float(args.arg or 24)
        r = call("GET", "/api/goals/report", args.service, params={"hours": hours})
        mins = round(r["working_seconds"] / 60)
        print(f"last {hours:g} h: {r['turns']} turns ({r['finished']} finished a goal), {r['failed']} failed, "
              f"{r['stepped_out']} stepped out for you · {mins} min of work")
        for key, b in r["by_backend"].items():
            print(f"  {key:<22} {int(b['turns'])} turns, {round(b['seconds'] / 60)} min")
        print(f"  on a subscription: {r['on_subscription']} turns")
        w = r.get("local_work")
        if w:
            print(_local_work_line(w))
            print("  " + " · ".join(f"{time.strftime('%a', time.strptime(d['day'], '%Y-%m-%d'))} {d['hours']:g} h"
                                    for d in w["by_day"]))
        return 0
    v = call("GET", "/api/goals", args.service)
    sh = v["shift"]
    print(f"background: {'on' if v['on'] else 'off'} · "
          f"{'whenever there is room' if v['when'] == 'resources' else 'only when you are away'}")
    print(f"now: {sh.get('state')} — {sh.get('why')}")
    if v.get("local_work"):
        print(_local_work_line(v["local_work"]))
    if not v["goals"]:
        print('no goals — eki goals add "…"   (or New goal on the board: http://127.0.0.1:8787/goals)')
    for g in v["goals"]:
        print(_goal_line(g))
    return 0


def _local_work_line(w: dict) -> str:
    """The Stage 3 measure in a line: useful local hours a day, against the 1% baseline."""
    return (f"local models, last {w['days']} days: {w['hours_a_day']:g} h of useful work a day "
            f"({w['share'] * 100:.1f}% of the time; {w['baseline_share'] * 100:g}% before the idle shift)")
