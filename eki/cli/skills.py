"""One skill store for Claude Code and Codex."""
from __future__ import annotations

from .. import skills

NAME = "skills"
HELP = "list, add or remove skills (shared by every program)"


def add(p) -> None:
    p.add_argument("action", choices=["list", "add", "remove", "sync"], nargs="?", default="list")
    p.add_argument("target", nargs="?", help="a skill folder (add) or name (remove)")


def run(args) -> int:
    if args.action == "add":
        print(f"added {skills.add(args.target)}")
        skills.sync_codex()
    elif args.action == "remove":
        print("removed" if skills.remove(args.target) else "no such skill")
    elif args.action == "sync":
        print("linked for Codex: " + (", ".join(skills.sync_codex()) or "nothing"))
    else:
        found = skills.all_skills()
        for s in found:
            print(f"{s['name']:<24} {s['description'][:70]}")
        if not found:
            print("no skills yet — eki-next skills add <folder with SKILL.md>")
    return 0
