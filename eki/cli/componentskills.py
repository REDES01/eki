"""`eki component skills add | remove` — the skills a project keeps in its `skills` card.

`<project>` is a project id or name as `eki project` lists them, or a folder; the card is
`components/skills/` of that project's root (created from the template on the first add).
`add` copies a skill folder or clones a git URL into `<card>/<name>/`, never overwriting
without --replace; `remove` deletes `<card>/<name>/`.
"""
from __future__ import annotations

from pathlib import Path

from .. import components, db, projectchats
from ..components import skillcard


def add(sub) -> None:
    s = sub.add_parser("skills", help="the project's skills card: add, remove")
    acts = s.add_subparsers(dest="skills_action", metavar="<add|remove>")
    a = acts.add_parser("add", help="copy a skill folder or git URL into the card (never overwrites)")
    a.add_argument("project", help="a project id or name, or a folder")
    a.add_argument("source", help="a skill folder (with SKILL.md) or a git URL")
    a.add_argument("--replace", action="store_true", help="replace a skill of the same name")
    r = acts.add_parser("remove", help="delete one skill from the card")
    r.add_argument("project", help="a project id or name, or a folder")
    r.add_argument("name")


def card_of(project: str) -> Path:
    """`components/skills/` of the project's root; ValueError when there's no such project."""
    conn = db.connect()
    folder = Path(project).expanduser()
    if not folder.is_dir():
        try:
            found = projectchats.folder_of(conn, projectchats.find(conn, project))
        except KeyError:
            raise ValueError(f"no project or folder {project}: `eki project` lists them")
        if not found:
            raise ValueError(f"project {project} has no folder yet")
        folder = Path(found)
    root = components.project_root(str(folder), conn)
    if root is None:
        raise ValueError(f"{folder} is not a folder")
    return root / "components" / "skills"


def _show(card: Path) -> None:
    good, broken = skillcard.held(card)
    for s in good:
        print(f"  {s['name']} — {s['description']}")
    for where, why in broken:
        print(f"broken: {where}: {why}")


def run(args) -> int:
    act = getattr(args, "skills_action", None)
    if act == "add":
        card = card_of(args.project)
        before = {p.name for p in card.iterdir()} if card.is_dir() else set()
        name = skillcard.add(card, args.source, replace=args.replace)
        verb = "replaced" if name in before else "added"
        print(f"{verb} {name} to {card}")
        _show(card)
        return 0
    if act == "remove":
        card = card_of(args.project)
        if not skillcard.remove(card, args.name):
            raise ValueError(f"no skill {args.name} in {card}")
        print(f"removed {args.name}")
        return 0
    print("eki component skills add <project> <folder|git URL> [--replace] | remove <project> <name>")
    return 0
