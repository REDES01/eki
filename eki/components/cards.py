"""Adding cards to a components/ folder and taking them away.

A card is a folder `components/<name>/`: a copy of a template, or a pool reference card holding
exactly `{"pool": "<name>"}`. Adding never overwrites; removing moves the folder to the macOS
Trash, never deletes. An `instructions` card writes the project's starter AGENTS.md and CLAUDE.md
when they are missing. Nothing here commits or stages anything: the folder is the person's.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .. import components, mcp, paths
from . import NAME, RESERVED, instructions


def templates() -> List[Path]:
    try:
        return sorted(d for d in components.templates_dir().iterdir() if d.is_dir() and not d.name.startswith((".", "_")))
    except OSError:
        return []


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not NAME.match(name) or name in RESERVED:
        raise ValueError(f"bad name {name!r}: lowercase letters, digits, - and _, not 'sessions'")


def _taken(p: Path) -> bool:
    return p.exists() or p.is_symlink()


def free_name(base: Path, stem: str) -> str:
    """`stem`, else `stem-2`, `stem-3`, … : the first not in base."""
    name, n = stem, 1
    while _taken(base / name):
        n += 1
        name = f"{stem}-{n}"
    return name


def register_mcp(spec: dict) -> List[str]:
    """Each of the spec's MCP servers into eki's registry, off; one already there is left alone.
    The servers leave `spec`."""
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


def add_template(base: Path, template: str, name: Optional[str] = None) -> Tuple[Path, List[str]]:
    """Copy the template into base/<name or a free name>; (the folder, what was said)."""
    temps = {t.name: t for t in templates()}
    src = temps.get(template)
    if src is None:
        raise ValueError(f"no template {template} (templates: {', '.join(temps) or 'none'})")
    base = Path(base)
    if name:
        _check_name(name)
        if _taken(base / name):
            raise ValueError(f"{base / name} already exists: nothing written (pick another --name)")
    else:
        _check_name(template)
        name = free_name(base, template)
    out = base / name
    base.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, out)
    f = out / "component.json"
    try:
        spec = json.loads(f.read_text())
    except (OSError, ValueError):
        spec = None
    said: List[str] = []
    if isinstance(spec, dict) and "mcp_servers" in spec:
        said = register_mcp(spec)
        f.write_text(json.dumps(spec, indent=2) + "\n")
    if isinstance(spec, dict) and spec.get("kind") == "instructions":
        if base.resolve() == components.pool_dir().resolve():
            said.append("a pool instructions card writes no files: attach it to a project")
        else:
            said += instructions.ensure_files(base.parent, spec.get("file", "AGENTS.md"))
    return out, said


def attach(base: Path, pool_name: str) -> Path:
    """A pool reference card base/<pool_name>/component.json = {"pool": pool_name}."""
    _check_name(pool_name)
    target = components.pool_dir() / pool_name
    if not (target / "component.json").is_file():
        raise ValueError(f"the pool has no component {pool_name}")
    try:
        components.load(target, "pool")
    except ValueError as e:
        raise ValueError(f"pool component {pool_name} is broken: {e}")
    out = Path(base) / pool_name
    if _taken(out):
        raise ValueError(f"{out} already exists: nothing written")
    out.mkdir(parents=True)
    (out / "component.json").write_text(json.dumps({"pool": pool_name}) + "\n")
    return out


def trash() -> Path:
    return paths.user_home() / ".Trash"


def to_trash(p: Path) -> Path:
    """Move p into the Trash under a free name; never deletes. Where it went."""
    bin_ = trash()
    bin_.mkdir(parents=True, exist_ok=True)
    dest = bin_ / p.name
    n = 1
    while _taken(dest):
        dest = bin_ / f'{p.stem} {time.strftime("%H.%M.%S")}{"" if n == 1 else f" {n}"}{p.suffix}'
        n += 1
    shutil.move(str(p), str(dest))
    return dest


def remove(base: Path, name: str) -> Path:
    """The card base/<name> to the Trash (a reference card alone, never the pool component)."""
    if not isinstance(name, str) or not name or name.startswith(".") or "/" in name:
        raise KeyError(f"no card {name!r}")
    p = Path(base) / name
    if not (p.is_dir() or p.is_symlink()):
        raise KeyError(f"no card {name} in {base}")
    return to_trash(p)
