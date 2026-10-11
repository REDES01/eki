"""`eki component kinds | ls | add | attach | rm | uses | show | knowledge | merge | skills` — the component folders.

`add` copies a template into `components/<name>/` of a project folder (the project of the cwd by
default, of -C folder, or the machine-wide pool with --pool), under the template's name or the
next free `-2`, `-3`…. `attach` adds a card reaching a pool component; `rm` moves a card to the
Trash. Nothing is overwritten, deleted, committed or staged: the folder is the person's to keep.
A template's `mcp_servers` go into eki's MCP registry, switched off, and leave the copy.
`skills add | remove` puts skills into or takes them out of a project's skills card
(componentskills.py). `merge` folds a project's cards into one (cardmerge.py).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

from .. import api_cardadd, components, db
from ..components import cardmerge, cards, knowledge, uses
from . import componentskills

NAME = "component"
HELP = "the components a project or the pool holds: kinds, ls, add, attach, rm, uses, show, knowledge, merge, skills"


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    sub.add_parser("kinds", help="each kind, and the templates add can copy")
    ls = sub.add_parser("ls", help="the pool and the folder's project components")
    ls.add_argument("folder", nargs="?", help="default: here")
    a = sub.add_parser("add", help="copy a template into components/<name>/ (never overwrites)")
    a.add_argument("template", help="a kind or a template (eki component kinds lists them)")
    a.add_argument("--name", help="the component's name (default: the template's, else -2, -3…)")
    where = a.add_mutually_exclusive_group()
    where.add_argument("--pool", action="store_true", help="into the machine-wide pool")
    where.add_argument("-C", dest="folder", help="into this folder's project (default: here)")
    at = sub.add_parser("attach", help="a card reaching a pool component: components/<name>/")
    at.add_argument("pool_name")
    at.add_argument("-C", dest="folder", help="this folder's project (default: here)")
    r = sub.add_parser("rm", help="move a card's folder to the Trash (never deletes)")
    r.add_argument("name")
    r.add_argument("-C", dest="folder", help="this folder's project (default: here)")
    u = sub.add_parser("uses", help="the last 20 calls of a component from this project")
    u.add_argument("name")
    u.add_argument("-C", dest="folder", help="this folder's project (default: here)")
    s = sub.add_parser("show", help="one component: folder, scope, component.json, notes")
    s.add_argument("name")
    k = sub.add_parser("knowledge", help="what a run in the project is told of its knowledge cards")
    k.add_argument("project", nargs="?", help="a project id or name, or a folder (default: here)")
    m = sub.add_parser("merge", help="fold a project's knowledge cards, or a service and an mcp card, into one")
    m.add_argument("project", help="a project id or name, or a folder")
    m.add_argument("kind", choices=["knowledge", "service"])
    m.add_argument("into", help="the merged card's name")
    m.add_argument("cards", nargs="*", help="knowledge: the cards (default: all); service: <service card> <mcp card>")
    m.add_argument("--about", help="the merged card's about line")
    m.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    componentskills.add(sub)


def _about(folder: Path) -> str:
    try:
        spec = json.loads((folder / "component.json").read_text())
        return f"{spec.get('kind', '?')}: {spec.get('about', '')}"
    except (OSError, ValueError, AttributeError) as e:
        return f"unreadable: {e}"


def _kinds() -> int:
    print("kinds:")
    for kind in components.KINDS:
        try:
            about = getattr(components.kind_module(kind), "ABOUT", "")
        except Exception as e:  # a broken kind module is reported, not raised
            about = f"broken: {e}"
        print(f"  {kind:<10} {about}")
    temps = cards.templates()
    print("templates:" if temps else "templates: none")
    for t in temps:
        print(f"  {t.name:<10} {_about(t)}")
    return 0


def _line(r: dict) -> str:
    if r["error"]:
        return f"{r['name']:<16} broken   {r['scope']:<8} {r['folder']}: {r['error']}"
    return f"{r['name']:<16} {r['kind']:<8} {r['scope']:<8} {r['about']}"


def _ls(folder: Optional[str]) -> int:
    rows = components.listing(folder or os.getcwd(), db.connect())
    if not rows:
        print("no components here or in the pool")
    for r in rows:
        print(_line(r))
        for n in r["notes"]:
            print(f"    {n}")
    return 0


def _root(folder: Optional[str]) -> Path:
    where = folder or os.getcwd()
    root = components.project_root(where, db.connect())
    if root is None:
        raise ValueError(f"{where} is not a folder")
    return root


def _add(args) -> int:
    if not args.pool and api_cardadd.is_workflow(args.template):    # one per project, from its settings
        out, said = api_cardadd.add_workflow(db.connect(), _root(args.folder), args.template, args.name)
    else:
        base = components.pool_dir() if args.pool else _root(args.folder) / "components"
        out, said = cards.add_template(base, args.template, args.name)
    print(f"wrote {out}")
    for line in said:
        print(line)
    _notes(out, "pool" if args.pool else "project")
    return 0


def _attach(args) -> int:
    out = cards.attach(_root(args.folder) / "components", args.pool_name)
    print(f"wrote {out / 'component.json'}: reaches the pool's {args.pool_name}")
    return 0


def _rm(args) -> int:
    try:
        dest = cards.remove(_root(args.folder) / "components", args.name)
    except KeyError as e:
        raise ValueError(e.args[0] if e.args else str(e))
    print(f"moved {args.name} to the Trash: {dest}")
    return 0


def _use_line(c: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(c["when"])) if c["when"] else "?"
    secs = f"{c['seconds']}s" if c["seconds"] is not None else "-"
    what = f"error: {c['error']}" if c["error"] else c["result"]
    return f"{when}  {c['caller']:<12} {c['state']:<8} {secs:>7}  {what}"


def _uses(args) -> int:
    conn = db.connect()
    got = uses.calls(conn, args.name, _root(args.folder), limit=20)
    if not got:
        print(f"no calls of {args.name} from here yet")
    for c in got:
        print(_use_line(c))
    return 0


def _notes(folder: Path, scope: str) -> None:
    try:
        c = components.load(folder, scope)
    except ValueError as e:
        print(f"not ready yet: {e}")
        return
    try:
        notes = list(components.kind_module(c.kind).notes(c) or [])
    except Exception as e:
        notes = [f"notes failed: {e}"]
    for n in notes:
        print(f"  {n}")


def _show(name: str) -> int:
    c = components.get(name, os.getcwd(), db.connect())
    print(f"{c.name}  {c.kind}  {c.scope}")
    print(f"folder: {c.folder}")
    print(json.dumps(c.spec, indent=2))
    _notes(c.folder, c.scope)
    return 0


def _knowledge(project: Optional[str]) -> int:
    root = componentskills.card_of(project or os.getcwd()).parent.parent
    print(knowledge.brief(str(root), db.connect()) or "no knowledge cards here")
    return 0


def _merge(args) -> int:
    base = componentskills.card_of(args.project).parent
    if args.kind == "service":
        if len(args.cards) != 2:
            raise ValueError("service merge takes <into> <service card> <mcp card>")
        plan = cardmerge.service(base, args.into, args.cards[0], args.cards[1], args.about)
    else:
        plan = cardmerge.knowledge(base, args.into, args.cards, args.about)
    if args.dry_run:
        print(f"dry run, nothing written ({base}):")
        for line in cardmerge.describe(plan):
            print(f"  {line}")
        return 0
    for line in cardmerge.apply(plan):
        print(line)
    print("nothing committed: the component.json files are yours to review and commit")
    return 0


def run(args) -> int:
    if args.action == "kinds":
        return _kinds()
    if args.action == "ls":
        return _ls(args.folder)
    if args.action == "add":
        return _add(args)
    if args.action == "attach":
        return _attach(args)
    if args.action == "rm":
        return _rm(args)
    if args.action == "uses":
        return _uses(args)
    if args.action == "show":
        return _show(args.name)
    if args.action == "knowledge":
        return _knowledge(args.project)
    if args.action == "merge":
        return _merge(args)
    if args.action == "skills":
        return componentskills.run(args)
    print("eki component kinds | ls [folder] | add <kind|template> [--name N] [--pool | -C folder]"
          " | attach <pool-name> [-C folder] | rm <name> [-C folder] | uses <name> [-C folder] | show <name>"
          " | merge <project> knowledge <into> [<card>…] | merge <project> service <into> <service> <mcp>"
          " | skills add <project> <folder|git URL> [--replace] | skills remove <project> <name>")
    return 0
