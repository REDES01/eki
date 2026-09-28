"""`eki project <owner>/<repo>` — a GitHub project in eki's own clone: its check, branch, setup retry."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .. import db, github, projects, projectsetup
from .common import conn, err

NAME = "project"
HELP = "a GitHub project eki works on in its own clone: show it, set its check or branch, retry its setup"


def add(p) -> None:
    p.add_argument("repo", help="OWNER/REPO")
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


def _find(c, repo: str) -> Optional[object]:
    if github.parse(f"https://github.com/{repo}") != repo:
        err(f"eki: {repo} isn't OWNER/REPO")
        return None
    p = projects.by_repo(c, repo)
    if p is None:
        err(f"eki: no project {repo}: label an issue eki there, or `eki goal add <its folder> \"…\"`")
        return None
    if p["state"] == "dropped":
        err(f"eki: {repo} is dropped: a new issue labelled eki brings it back")
        return None
    return p


def run(args) -> int:
    c = conn()
    p = _find(c, args.repo)
    if p is None:
        return 1
    if args.check is not None:
        set_check(c, p, args.check)
        print(f"{args.repo}: check {args.check or 'none'} (set)")
    if args.branch:
        set_branch(c, p, args.branch)
        print(f"{args.repo}: branch {args.branch}")
    if args.retry:
        try:
            print(projectsetup.retry(c, args.repo))
        except ValueError as e:
            err(f"eki: {e}")
            return 1
    if args.check is None and not args.branch and not args.retry:
        for line in block(c, projects.get(c, p["id"])):
            print(line)
    return 0
