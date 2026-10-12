"""`eki project [key]` — every project, or one: its folder and chats; on GitHub its check, branch, setup retry;
`eki project rm <key>` deletes one (your folder is never touched)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .. import api_projects, db, projectchats, projectdelete, projects, projectsetup
from . import projectlist
from .common import conn, err

NAME = "project"
HELP = ("list your projects, show or make one, set its folder, delete one (`rm`); "
        "on GitHub: its check, branch, setup retry")


def add(p) -> None:
    p.add_argument("key", nargs="?", help="a project's name, id, OWNER/REPO or eki; none lists them all; "
                                          "`describe <key> TEXT` sets what it's for; `rm <key>` deletes it")
    p.add_argument("more", nargs="*", metavar="ARG", help="with `describe`: the project and its text ('' clears it); "
                                                       "with `rm`: the project")
    p.add_argument("--new", metavar="NAME", help="make a project (folder ~/NAME unless --folder)")
    p.add_argument("--folder", metavar="PATH", help="the folder the project's chats run in")
    p.add_argument("--description", metavar="TEXT", help="with --new: what you're trying to achieve")
    p.add_argument("--check", help="the shell command that judges an item ('' for none); replaces the guess")
    p.add_argument("--branch", help="the branch eki works from and opens PRs against")
    p.add_argument("--autonomy", choices=projects.AUTONOMY,
                   help="a local project: apply (eki lands fit items on its branch) or propose (the branch waits)")
    p.add_argument("--retry", action="store_true", help="start the failed setup step (clone or install) again")
    p.add_argument("--yes", action="store_true", help="with `rm`: delete without asking")


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


def set_autonomy(c, pid: str, name: str, mode: str) -> int:
    try:
        projects.set_autonomy(c, pid, mode)
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    print(f"{name}: autonomy {mode}")
    return 0


def describe(c, key: str, text: str) -> int:
    pid = projectlist.resolve(c, key)
    if pid is None:
        return 1
    try:
        got = projectchats.set_description(c, pid, text)
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    print(f"{key}: {got or 'no description'}")
    return 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def remove(c, key: str, yes: bool) -> int:
    """Delete a project after asking once (unless --yes); say what stopped, archived and went."""
    try:
        pre = projectdelete.preview(c, key)
    except ValueError as e:
        err(f"eki: {e}")
        return 1
    except KeyError:
        projectlist.resolve(c, key)                 # says why, as the other subcommands do
        return 1
    if not yes:
        print(pre["line"])
        try:
            reply = input(f"Delete {pre['name']}? [y/N] ")
        except EOFError:
            reply = ""
        if reply.strip().lower() not in ("y", "yes"):
            print("kept")
            return 1
    try:
        done = projectdelete.delete(c, pre["id"])
    except (ValueError, KeyError) as e:
        err(f"eki: {str(e).strip(chr(39))}")
        return 1
    print(f"deleted {done['name']}")
    print("  stopped: " + ", ".join([_plural(done["runs"], "run"), _plural(done["goals"], "goal"),
                                     _plural(done["items"], "item"), _plural(done["standing"], "standing goal"),
                                     _plural(done["asks"], "ask")]))
    print(f"  archived: {_plural(done['chats'], 'chat')}")
    for path in done["removed"]:
        print(f"  removed: {path}")
    print(f"  your folder {done['folder'] or '(none)'} is not touched")
    return 0


def run(args) -> int:
    c = conn()
    if args.key == "rm":
        if len(args.more) != 1:
            err("eki: eki project rm <name|id|OWNER/REPO> [--yes]")
            return 1
        return remove(c, args.more[0], args.yes)
    if args.yes:
        err("eki: --yes goes with `eki project rm <key>`")
        return 1
    if args.key == "describe" and args.more:
        if len(args.more) != 2:
            err('eki: eki project describe <key> "text"')
            return 1
        return describe(c, *args.more)
    if args.more:
        err(f"eki: unexpected {' '.join(args.more)}")
        return 1
    github_only = args.check is not None or args.branch or args.retry
    if args.new is not None:
        if args.key or github_only or args.autonomy:
            err("eki: --new takes a name and maybe --folder and --description, nothing else")
            return 1
        return projectlist.new(c, args.new, args.folder, args.description)
    if args.description is not None:
        err('eki: --description goes with --new; for a project: eki project describe <key> "text"')
        return 1
    if not args.key:
        if args.folder or github_only or args.autonomy:
            err("eki: say which project: eki project <key> --folder/--check/--branch/--retry/--autonomy")
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
    if args.autonomy and p is None:
        err("eki: eki lands its own work through its queue: --autonomy is for your projects")
        return 1
    if args.folder and projectlist.set_folder(c, pid, args.folder):
        return 1
    if args.autonomy and set_autonomy(c, pid, p["name"], args.autonomy):
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
    if not args.folder and not github_only and not args.autonomy:
        e = projectlist.entry_of(c, pid)
        if p is not None and p["repo"]:
            print("\n".join(block(c, projects.get(c, pid))))
        else:
            print(e["name"])
            if p is not None:
                print(f"  autonomy: {projects.autonomy(p)}")
                landing = api_projects.landing_line(p)
                if landing:                         # a local project only; a GitHub row says it in block()
                    print(f"  lands: {landing}")
        print("\n".join(projectlist.details(e)))
    return 0
