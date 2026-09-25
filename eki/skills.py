"""One skill store for every program.

Skills are Agent Skills folders (`<name>/SKILL.md`) in `EKI_HOME/skills`.
Each program is handed the same store its own way:

- Claude Code: per run, as a plugin (`--plugin-dir`) whose skills folder
  is the store — nothing is written into `~/.claude`.
- Codex: it reads user skills from `~/.agents/skills`, so each skill is
  linked there. Only links eki made are ever changed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from . import paths


def agents_dir() -> Path:
    return Path(os.environ.get("EKI_AGENTS_SKILLS") or "~/.agents/skills").expanduser()


def _front(text: str) -> Dict[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out: Dict[str, str] = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def all_skills() -> List[Dict[str, str]]:
    found = []
    for d in sorted(paths.skills().iterdir()):
        md = d / "SKILL.md"
        if md.is_file():
            meta = _front(md.read_text(errors="replace"))
            found.append({"name": d.name, "description": meta.get("description", ""), "path": str(d)})
    return found


def add(source: str) -> str:
    src = Path(source).expanduser().resolve()
    if src.is_file() and src.name == "SKILL.md":
        src = src.parent
    md = src / "SKILL.md"
    if not md.is_file():
        raise ValueError(f"{src} has no SKILL.md")
    name = _front(md.read_text(errors="replace")).get("name") or src.name
    name = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    dest = paths.skills() / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return name


def remove(name: str) -> bool:
    dest = paths.skills() / name
    if not dest.exists():
        return False
    shutil.rmtree(dest)
    sync_codex()
    return True


def claude_plugin() -> Optional[str]:
    """A plugin folder that is the store, for `claude --plugin-dir`."""
    if not all_skills():
        return None
    root = paths.home() / "claude-plugin"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"name": "eki", "version": "1.0.0",
                                    "description": "Skills from eki's store"}, indent=2))
    link = root / "skills"
    if not link.is_symlink():
        if link.exists():
            shutil.rmtree(link)
        link.symlink_to(paths.skills(), target_is_directory=True)
    return str(root)


def sync_codex() -> List[str]:
    """Link every skill into ~/.agents/skills; drop links to skills that are gone.
    A name that's already taken by something eki didn't make is left alone."""
    target = agents_dir()
    target.mkdir(parents=True, exist_ok=True)
    store = paths.skills().resolve()
    linked = []
    for s in all_skills():
        link = target / s["name"]
        if link.is_symlink() and Path(os.readlink(link)).resolve().parent == store:
            linked.append(s["name"])
            continue
        if not link.exists() and not link.is_symlink():
            link.symlink_to(Path(s["path"]), target_is_directory=True)
            linked.append(s["name"])
    for link in target.iterdir():
        if link.is_symlink():
            dest = Path(os.readlink(link))
            if dest.parent.resolve() == store and not dest.exists():
                link.unlink()
    return linked
