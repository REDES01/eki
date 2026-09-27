"""`eki goal` — standing goals: a goal in words on a git folder, worked on in rounds (eki/standing.py)."""
from __future__ import annotations

import time
from typing import List, Optional

from .. import budget, projects, standing
from .common import conn, err

NAME = "goal"
HELP = "standing goals: add one on a git folder, list them, pause, resume, drop, or start a round now"

#: an item in one of these is done with: not listed under its standing goal
OVER = ("applied", "dropped", "left", "unfit", "landed", "live", "rolled back")


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    a = sub.add_parser("add", help="a standing goal on a git folder")
    a.add_argument("folder", help="the git folder (at least one commit)")
    a.add_argument("text", help="the goal, in words")
    a.add_argument("--check", help="the shell command that judges an item (default: its bin/check)")
    a.add_argument("--branch", help="the branch eki works from (default: the folder's current one)")
    for action, text in (("pause", "stop opening rounds"), ("resume", "on again, stuck and rest cleared"),
                         ("drop", "never again"), ("now", "clear the rest: a round when the Mac and budget allow")):
        sub.add_parser(action, help=text).add_argument("id", help="the standing goal's id (or its start)")


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def lines(c, t: Optional[float] = None) -> List[str]:
    """The listing: each standing goal that isn't dropped, then the budget right now."""
    t = time.time() if t is None else t
    out: List[str] = []
    for st in c.execute("SELECT * FROM standing WHERE state!='dropped' ORDER BY created_at").fetchall():
        project = projects.get(c, st["project"]) if st["project"] else None
        out.append(f"{st['id']}  {project['name'] if project else 'eki'}  {st['state']}  {_first(st['text'])}")
        if (st["rest_until"] or 0) > t:
            why = "resting until " + time.strftime("%a %H:%M", time.localtime(st["rest_until"]))
            if st["why"]:
                why += f" — {st['why']}"
        else:
            why = st["why"] or "-"
        out.append(f"  why: {why}")
        out.append(f"  rounds: {st['rounds']}")
        marks = ",".join("?" * len(OVER))
        for g in c.execute("SELECT id FROM goals WHERE standing_id=? AND state IN ('drafting','planning') "
                           "ORDER BY created_at", (st["id"],)).fetchall():
            out.append(f"  round {g['id']} planning")
        items = c.execute(f"SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.standing_id=? "
                          f"AND i.state NOT IN ({marks}) ORDER BY i.created_at", (st["id"], *OVER)).fetchall()
        for it in items:
            branch = it["branch"] or (f"eki/{it['id']}" if project else f"self/{it['id']}")
            line = f"  {it['id']}  {it['state']:<9} {branch}  {it['title']}"
            out.append(line)
            if it["state"] == "proposed":
                hint = f"git -C {project['path']} merge {branch}" if project else f"eki self apply {it['id']}"
                out.append(f"      {hint}")
    if not out:
        out.append('no standing goals: eki goal add <folder> "…"')
    out.append(budget.describe(t))
    return out


def run(args) -> int:
    c = conn()
    action = getattr(args, "action", None)
    if action == "add":
        try:
            sid = standing.add(c, args.folder, args.text, check=args.check, branch=args.branch)
        except ValueError as e:
            err(f"eki: {e}")
            return 1
        print(sid)
        return 0
    if action in ("pause", "resume", "drop", "now"):
        sid = getattr(standing, action)(c, args.id)
        said = {"pause": "paused", "resume": "on", "drop": "dropped",
                "now": "rest cleared: a round opens when the Mac and the budget allow"}[action]
        print(f"{sid}: {said}")
        return 0
    for line in lines(c):
        print(line)
    return 0
