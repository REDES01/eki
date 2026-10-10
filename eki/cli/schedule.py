"""`eki schedule` — scheduled prompts: a prompt sent as a new chat on a clock (eki/schedule.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from .. import schedule, targets
from .common import conn

NAME = "schedule"
HELP = "scheduled prompts: add one on a clock, list them, pause, resume, drop, or run one now"


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    a = sub.add_parser("add", help="a prompt sent as a new chat on a clock")
    a.add_argument("text", help="the prompt")
    a.add_argument("--every", required=True, choices=schedule.EVERY, help="how often")
    a.add_argument("--at", help="HH:MM, in this Mac's zone (daily, weekdays, weekly)")
    a.add_argument("--minute", type=int, default=0, help="the minute past each hour (hourly; default 0)")
    a.add_argument("--day", choices=schedule.DAYS, help="the day (weekly)")
    a.add_argument("--project", help="the project its chats belong to (id or name; it needs a folder)")
    a.add_argument("--to", help="send it to this provider or provider:model (default: Auto, routing decides)")
    a.add_argument("--effort", help="the effort to ask for (needs --to)")
    sub.add_parser("list", help="every schedule not dropped")
    for action, text in (("pause", "stop firing"), ("resume", "on again, the next firing counted from now"),
                         ("drop", "never again"), ("run", "fire it now; the next firing stays")):
        sub.add_parser(action, help=text).add_argument("id", help="the schedule's id (or its start)")


def cadence(args) -> Dict[str, Any]:
    """The cadence JSON from the flags; ValueError when a needed one is missing."""
    if args.every == "hourly":
        return {"every": "hourly", "minute": args.minute}
    if not args.at:
        raise ValueError(f"--every {args.every} needs --at HH:MM")
    out: Dict[str, Any] = {"every": args.every, "at": args.at}
    if args.every == "weekly":
        if not args.day:
            raise ValueError("--every weekly needs --day mon..sun")
        out["day"] = args.day
    return out


def target(args) -> Any:
    """The target in targets.fmt's form, or None for Auto."""
    if not args.to:
        if args.effort:
            raise ValueError("--effort needs --to")
        return None
    t = targets.parse(args.to)
    if args.effort:
        t.effort = args.effort.strip()
    return targets.fmt(t)


def when(t: Optional[float], tz: str) -> str:
    if not t:
        return "-"
    return datetime.fromtimestamp(t, ZoneInfo(tz)).strftime("%a %d %b %H:%M")


def lines(c) -> list:
    out = []
    for s in schedule.listing(c):
        tgt = f"  to {targets.key(s['target'])}" if s["target"] else ""
        out.append(f"{s['id']}  {s['state']:<6} {s['cadence_text']}  next {when(s['next_at'], s['tz'])}  "
                   f"last {when(s['last_at'], s['tz'])}{tgt}")
        out.append(f"  {(s['text'].splitlines() or [''])[0][:100]}")
        if s["last_thread"]:
            out.append(f"  last chat: {s['last_thread']}")
        out.append(f"  why: {s['why'] or '-'}")
    if not out:
        out.append('no scheduled prompts: eki schedule add "…" --every daily --at 09:00')
    return out


def run(args) -> int:
    c = conn()
    action = getattr(args, "action", None)
    if action == "add":
        sid = schedule.add(c, args.text, cadence(args), project=args.project, target=target(args))
        s = schedule.find(c, sid)
        print(sid)
        print(f"  {schedule.cadence_text(s['cadence'])} ({s['tz']}), next {when(s['next_at'], s['tz'])}")
        return 0
    if action in ("pause", "resume", "drop"):
        sid = getattr(schedule, action)(c, args.id)
        print(f"{sid}: {({'pause': 'paused', 'resume': 'on', 'drop': 'dropped'})[action]}")
        return 0
    if action == "run":
        tid = schedule.run_now(c, args.id)
        print(f"{schedule.find(c, args.id)['id']}: fired, chat {tid}")
        return 0
    for line in lines(c):
        print(line)
    return 0
