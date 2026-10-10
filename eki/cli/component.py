"""`eki component kinds | ls [folder] | add <kind|template> | show <name>` — the component folders.

`add` copies a template into `components/<name>/` of a project folder (the project of the cwd by
default, of -C folder, or the machine-wide pool with --pool). It never overwrites and never
commits or stages anything: the folder is the person's to keep. A template's `mcp_servers` go
into eki's MCP registry, switched off, and leave the copied component.json.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

from .. import components, db, mcp

NAME = "component"
HELP = "the components a project or the pool holds: kinds, ls, add from a template, show"


def add(p) -> None:
    sub = p.add_subparsers(dest="action", metavar="<action>")
    sub.add_parser("kinds", help="each kind, and the templates add can copy")
    ls = sub.add_parser("ls", help="the pool and the folder's project components")
    ls.add_argument("folder", nargs="?", help="default: here")
    a = sub.add_parser("add", help="copy a template into components/<name>/ (never overwrites)")
    a.add_argument("template", help="a kind or a template (eki component kinds lists them)")
    a.add_argument("--name", help="the component's name (default: the template's)")
    where = a.add_mutually_exclusive_group()
    where.add_argument("--pool", action="store_true", help="into the machine-wide pool")
    where.add_argument("-C", dest="folder", help="into this folder's project (default: here)")
    s = sub.add_parser("show", help="one component: folder, scope, component.json, notes")
    s.add_argument("name")


def _templates() -> List[Path]:
    base = components.templates_dir()
    try:
        return sorted(d for d in base.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))
    except OSError:
        return []


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
    temps = _templates()
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


def _dest(args) -> Path:
    if args.pool:
        return components.pool_dir()
    where = args.folder or os.getcwd()
    root = components.project_root(where, db.connect())
    if root is None:
        raise ValueError(f"{where} is not a folder")
    return root / "components"


def _register_mcp(spec: dict) -> List[str]:
    """Each of the template's MCP servers into eki's registry, off; one already there is left alone."""
    said = []
    have = {s["name"] for s in mcp.all_servers()}
    for name, s in (spec.pop("mcp_servers", None) or {}).items():
        if name in have:
            said.append(f"MCP server {name} is already in eki's registry: kept as it is")
            continue
        s = s if isinstance(s, dict) else {}
        mcp.add(name, command=s.get("command"), args=s.get("args"), url=s.get("url"), env=s.get("env"))
        mcp.set_on(name, False)
        said.append(f"MCP server {name} added to eki's registry, off (eki mcp on {name})")
    return said


def _add(args) -> int:
    temps = {t.name: t for t in _templates()}
    src = temps.get(args.template)
    if src is None:
        raise ValueError(f"no template {args.template} (templates: {', '.join(temps) or 'none'})")
    name = args.name or args.template
    if not components.NAME.match(name) or name in components.RESERVED:
        raise ValueError(f"bad name {name!r}: lowercase letters, digits, - and _, not 'sessions'")
    base = _dest(args)
    out = base / name
    if out.exists():
        raise ValueError(f"{out} already exists: nothing written (pick another --name)")
    base.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, out)
    said = []
    f = out / "component.json"
    try:
        spec = json.loads(f.read_text())
    except (OSError, ValueError):
        spec = None
    if isinstance(spec, dict) and "mcp_servers" in spec:
        said = _register_mcp(spec)
        f.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {out}")
    for line in said:
        print(line)
    _notes(out, "pool" if args.pool else "project")
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


def run(args) -> int:
    if args.action == "kinds":
        return _kinds()
    if args.action == "ls":
        return _ls(args.folder)
    if args.action == "add":
        return _add(args)
    if args.action == "show":
        return _show(args.name)
    print("eki component kinds | ls [folder] | add <kind|template> [--name N] [--pool | -C folder] | show <name>")
    return 0
