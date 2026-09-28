"""`eki goal` — standing goals: a goal in words on a git folder, worked on in rounds (eki/standing.py)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from .. import budget, github, projects, projectsetup, standing
from . import project as projectcli
from .common import conn, err

NAME = "goal"
HELP = ("standing goals: add one on a git folder, list them and the GitHub projects, "
        "pause, resume, drop (a goal, or OWNER/REPO), or start a round now")

#: an item in one of these is done with: not listed under its standing goal
OVER = ("applied", "dropped", "left", "unfit", "landed", "live", "rolled back")


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    a = sub.add_parser("add", help="a standing goal on a git folder (on GitHub: on eki's own clone of it)")
    a.add_argument("folder", help="the git folder (at least one commit)")
    a.add_argument("text", nargs="?", help="the goal, in words")
    a.add_argument("--issues", action="store_true", help="not needed: issues labelled eki in any repo you own "
                   "are taken")
    a.add_argument("--check", help="the shell command that judges an item (default: its bin/check, or the guess)")
    a.add_argument("--branch", help="the branch eki works from (default: the folder's current one)")
    for action, text in (("pause", "stop opening rounds"), ("resume", "on again, stuck and rest cleared"),
                         ("now", "clear the rest: a round when the Mac and budget allow")):
        sub.add_parser(action, help=text).add_argument("id", help="the standing goal's id (or its start)")
    sub.add_parser("drop", help="never again: a standing goal, or all work on OWNER/REPO").add_argument(
        "id", help="the standing goal's id (or its start), or OWNER/REPO")

ISSUES_NOTE = "not needed: issues labelled eki in any repo you own are taken"


def _first(text: Optional[str]) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def path_line(project) -> str:
    """Which way work comes in and goes out for a project — checked fresh, no gh off GitHub."""
    repo, why = github.path_of(project["path"])
    if repo is None:
        return f"  path: local — merge by hand ({why})"
    return f"  path: GitHub {repo} — issues labelled eki in, PRs out"


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
    """The listing: each standing goal that isn't dropped, each GitHub project (OWNER/REPO) that isn't,
    each folder project with issue goals, then the budget."""
    t = time.time() if t is None else t
    out: List[str] = []
    shown = set()
    for st in c.execute("SELECT * FROM standing WHERE state!='dropped' ORDER BY created_at").fetchall():
        project = projects.get(c, st["project"]) if st["project"] else None
        issue = f"#{st['issue']} " if st["issue"] else ""
        out.append(f"{st['id']}  {project['name'] if project else 'eki'}  {st['state']}  "
                   f"{issue}{_first(st['text'])}")
        if project is not None and project["id"] not in shown and not project["repo"]:
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
        if project["id"] in shown or project["state"] == "dropped":
            continue
        if project["repo"]:
            out.extend(projectcli.block(c, project))
            continue
        issue_goals = _issue_goals(c, project)
        if not issue_goals:
            continue
        out.append(f"{project['name']}  {project['path']}")
        out.append(path_line(project))
        out.extend(issue_goals)
    if not out:
        out.append('no standing goals: eki goal add <folder> "…"')
    out.append(budget.describe(t))
    return out


def _add(c, args) -> int:
    if args.issues:
        print(ISSUES_NOTE)
    if not args.text:
        if args.issues:
            return 0
        err("eki: say the goal in words")
        return 1
    folder = Path(args.folder).expanduser().resolve()
    repo = None if projects.is_self(folder) or not folder.is_dir() else github.repo_of(folder)
    try:
        if repo is None:
            print(standing.add(c, args.folder, args.text, check=args.check, branch=args.branch))
            return 0
        p = projectsetup.ensure(c, repo)
        if args.check is not None:
            projectcli.set_check(c, p, args.check)
        if args.branch:
            projectcli.set_branch(c, p, args.branch)
        sid = standing.add(c, str(folder), args.text, project=p["id"])
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    print(f"{repo}: working in eki's own clone at {projects.clone_path(repo)}, not {folder}")
    print(sid)
    return 0


def _drop(c, arg: str) -> int:
    """OWNER/REPO: all work on the project stops and its clone goes; otherwise a standing goal."""
    if "/" not in arg:
        print(f"{standing.drop(c, arg)}: dropped")
        return 0
    try:
        print(projectsetup.drop(c, arg))
    except KeyError as e:
        err(f"eki: {e.args[0]}")
        return 1
    return 0


def run(args) -> int:
    c = conn()
    action = getattr(args, "action", None)
    if action == "add":
        return _add(c, args)
    if action == "drop":
        return _drop(c, args.id)
    if action in ("pause", "resume", "now"):
        sid = getattr(standing, action)(c, args.id)
        said = {"pause": "paused", "resume": "on",
                "now": "rest cleared: a round opens when the Mac and the budget allow"}[action]
        print(f"{sid}: {said}")
        return 0
    for line in lines(c):
        print(line)
    return 0
