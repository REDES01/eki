"""`eki project [key]` — every project, or one: its folder and chats; on GitHub its check, branch, setup retry."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .. import db, projectchats, projects, projectsetup
from . import projectlist
from .common import conn, err

NAME = "project"
HELP = "list your projects, show or make one, set its folder; on GitHub: its check, branch, setup retry"


def add(p) -> None:
    p.add_argument("key", nargs="?", help="a project's name, id, OWNER/REPO or eki; none lists them all")
    p.add_argument("--new", metavar="NAME", help="make a project (folder ~/NAME unless --folder)")
    p.add_argument("--folder", metavar="PATH", help="the folder the project's chats run in")
    p.add_argument("--check", help="the shell command that judges an item ('' for none); replaces the guess")
    p.add_argument("--branch", help="the branch eki works from and opens PRs against")
    p.add_argument("--retry", action="store_true", help="start the failed setup step (clone or install) again")


def setup_line(project) -> str:
    if project["state"] == "retired":
        return f"  setup: retired — finishing in {project['path']}"
    if project["setup"] == "fault":
        return f"  setup: fault: {project['fault'] or '?'}"
    return f"  setup: {project['setup'] or '-'}"


def open_prs(c, project) -> List[str]:
    rows = c.execute("SELECT i.pr, i.title FROM items i JOIN goals g ON g.id=i.goal_id WHERE g.project=? "
                     "AND i.pr_state='open' AND i.pr IS NOT NULL ORDER BY i.created_at",
                     (project["id"],)).fetchall()
    return [f"  PR {r['pr']}  {r['title']}" for r in rows]


def block(c, project) -> List[str]:
    """A project on GitHub as `eki goal` and `eki project` show it."""
    from .goal import _issue_goals, path_line
    out = [project["repo"]]
    out.append(f"  check: {project['check_cmd'] or 'none'} ({project['check_from'] or '-'})")
    if project["state"] != "retired":
        out.append(f"  clone: {project['path']}")
    out.append(setup_line(project))
    if Path(project["path"]).is_dir():
        out.append(path_line(project))
    else:
        out.append(f"  path: GitHub {project['repo']} — once the clone lands")
    out.extend(_issue_goals(c, project))
    out.extend(open_prs(c, project))
    return out


def set_check(c, project, check: str) -> None:
    """The person's check replaces the guess, and no later pass guesses again; '' is no check."""
    with db.tx(c):
        c.execute("UPDATE projects SET check_cmd=?, check_from='set' WHERE id=?", (check or None, project["id"]))


def set_branch(c, project, branch: str) -> None:
    with db.tx(c):
        c.execute("UPDATE projects SET branch=? WHERE id=?", (branch, project["id"]))


def run(args) -> int:
    c = conn()
    github_only = args.check is not None or args.branch or args.retry
    if args.new is not None:
        if args.key or github_only:
            err("eki: --new takes a name and maybe --folder, nothing else")
            return 1
        return projectlist.new(c, args.new, args.folder)
    if not args.key:
        if args.folder or github_only:
            err("eki: say which project: eki project <key> --folder/--check/--branch/--retry")
            return 1
        return projectlist.listing(c)
    pid = projectlist.resolve(c, args.key)
    if pid is None:
        return 1
    p = None if pid == projectchats.SELF else projects.get(c, pid)
    if github_only and (p is None or not p["repo"]):
        name = "eki" if p is None else p["name"]
        err(f"eki: {name} isn't on GitHub: --check, --branch and --retry are GitHub-only")
        return 1
    if args.folder and projectlist.set_folder(c, pid, args.folder):
        return 1
    if args.check is not None:
        set_check(c, p, args.check)
        print(f"{p['repo']}: check {args.check or 'none'} (set)")
    if args.branch:
        set_branch(c, p, args.branch)
        print(f"{p['repo']}: branch {args.branch}")
    if args.retry:
        try:
            print(projectsetup.retry(c, p["repo"]))
        except ValueError as e:
            err(f"eki: {e}")
            return 1
    if not args.folder and not github_only:
        e = projectlist.entry_of(c, pid)
        if p is not None and p["repo"]:
            print("\n".join(block(c, projects.get(c, pid))))
        else:
            print(e["name"])
        print("\n".join(projectlist.details(e)))
    return 0
