"""`eki goal` — standing goals: a goal in words on a git folder, worked on in rounds (eki/standing.py)."""
from __future__ import annotations

import time
from typing import List, Optional

from .. import budget, github, projects, standing
from .common import conn, err

NAME = "goal"
HELP = ("standing goals: add one on a git folder (or watch its GitHub issues), list them, "
        "pause, resume, drop, or start a round now")

#: an item in one of these is done with: not listed under its standing goal
OVER = ("applied", "dropped", "left", "unfit", "landed", "live", "rolled back")


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    a = sub.add_parser("add", help="a standing goal on a git folder, or watch its issues")
    a.add_argument("folder", help="the git folder (at least one commit)")
    a.add_argument("text", nargs="?", help="the goal, in words (optional with --issues)")
    a.add_argument("--issues", action="store_true",
                   help="take work from its GitHub issues labelled eki, hand it back as PRs")
    a.add_argument("--check", help="the shell command that judges an item (default: its bin/check)")
    a.add_argument("--branch", help="the branch eki works from (default: the folder's current one)")
    for action, text in (("pause", "stop opening rounds"), ("resume", "on again, stuck and rest cleared"),
                         ("drop", "never again"), ("now", "clear the rest: a round when the Mac and budget allow")):
        sub.add_parser(action, help=text).add_argument("id", help="the standing goal's id (or its start)")
    sub.add_parser("unwatch", help="stop taking work from a folder's GitHub issues").add_argument(
        "folder", help="the git folder")


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def path_line(project) -> str:
    """Which way work comes in and goes out for a project — checked fresh, no gh off GitHub."""
    repo, why = github.path_of(project["path"])
    if repo is None:
        return f"  path: local — merge by hand ({why})"
    watching = ", watching issues" if project["issues"] else ""
    return f"  path: GitHub {repo} — issues labelled eki in, PRs out{watching}"


def _items(c, where: str, arg: str, project) -> List[str]:
    """The unfinished items of the goals matching `where`, with how to take a proposed one."""
    out: List[str] = []
    marks = ",".join("?" * len(OVER))
    items = c.execute(f"SELECT i.* FROM items i JOIN goals g ON g.id=i.goal_id WHERE {where} "
                      f"AND i.state NOT IN ({marks}) ORDER BY i.created_at", (arg, *OVER)).fetchall()
    for it in items:
        branch = it["branch"] or (f"eki/{it['id']}" if project else f"self/{it['id']}")
        out.append(f"  {it['id']}  {it['state']:<9} {branch}  {it['title']}")
        if it["state"] == "proposed":
            if it["pr"]:
                hint = it["pr"]
            else:
                hint = f"git -C {project['path']} merge {branch}" if project else f"eki self apply {it['id']}"
            out.append(f"      {hint}")
    return out


def _issue_goals(c, project) -> List[str]:
    """A project's goals from GitHub issues that are still being worked, with their open items."""
    out: List[str] = []
    for g in c.execute("SELECT * FROM goals WHERE project=? AND issue IS NOT NULL AND standing_id IS NULL "
                       "ORDER BY created_at", (project["id"],)).fetchall():
        items = _items(c, "g.id=?", g["id"], project)
        if not items and g["state"] not in ("drafting", "planning"):
            continue
        out.append(f"  #{g['issue']} {_first(g['text'])} — goal {g['id']} {g['state']}")
        out.extend(items)
    return out


def lines(c, t: Optional[float] = None) -> List[str]:
    """The listing: each standing goal that isn't dropped, each watched project, then the budget."""
    t = time.time() if t is None else t
    out: List[str] = []
    shown = set()
    for st in c.execute("SELECT * FROM standing WHERE state!='dropped' ORDER BY created_at").fetchall():
        project = projects.get(c, st["project"]) if st["project"] else None
        issue = f"#{st['issue']} " if st["issue"] else ""
        out.append(f"{st['id']}  {project['name'] if project else 'eki'}  {st['state']}  "
                   f"{issue}{_first(st['text'])}")
        if project is not None and project["id"] not in shown:
            shown.add(project["id"])
            out.append(path_line(project))
            out.extend(_issue_goals(c, project))
        if (st["rest_until"] or 0) > t:
            why = "resting until " + time.strftime("%a %H:%M", time.localtime(st["rest_until"]))
            if st["why"]:
                why += f" — {st['why']}"
        else:
            why = st["why"] or "-"
        out.append(f"  why: {why}")
        out.append(f"  rounds: {st['rounds']}")
        for g in c.execute("SELECT id FROM goals WHERE standing_id=? AND state IN ('drafting','planning') "
                           "ORDER BY created_at", (st["id"],)).fetchall():
            out.append(f"  round {g['id']} planning")
        out.extend(_items(c, "g.standing_id=?", st["id"], project))
    for project in projects.all(c):
        if project["id"] in shown:
            continue
        issue_goals = _issue_goals(c, project)
        if not project["issues"] and not issue_goals:
            continue
        out.append(f"{project['name']}  {project['path']}")
        out.append(path_line(project))
        out.extend(issue_goals)
    if not out:
        out.append('no standing goals: eki goal add <folder> "…"')
    out.append(budget.describe(t))
    return out


def _add(c, args) -> int:
    if not args.text and not args.issues:
        err("eki: say the goal in words, or --issues to take work from the folder's GitHub issues")
        return 1
    try:
        if args.issues:
            repo, why = github.path_of(args.folder)
            if repo is None:
                err(f"eki: {args.folder} can't watch issues: {why}")
                return 1
            pid = projects.watch(c, args.folder, check=args.check, branch=args.branch)
            if not args.text:
                print(f"{pid}: watching issues labelled eki on {repo}")
                return 0
        print(standing.add(c, args.folder, args.text, check=args.check, branch=args.branch))
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    return 0


def run(args) -> int:
    c = conn()
    action = getattr(args, "action", None)
    if action == "add":
        return _add(c, args)
    if action == "unwatch":
        try:
            pid = projects.unwatch(c, args.folder)
        except KeyError as e:
            err(f"eki: {e.args[0]}")
            return 1
        print(f"{pid}: not watching issues")
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
