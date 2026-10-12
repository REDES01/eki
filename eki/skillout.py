"""Skills, eki -> client: eki's own skills are mirrored to the outbox.

A store skill is eki's own when `skill-sources.json` gives it source
'learned' or 'edited', or source 'claude' with a store copy whose tree hash
differs from the import baseline. Each one is copied, whole folder, to
`sync/outbox/skills/<name>/` when the copy there is missing or differs; the
client task proposes it to the owner and consumes it. eki never deletes an
outbox entry and writes nothing anywhere else. A file marked sensitive
(`eki/sensitive.py`) is left out of the copy, and the tick says so.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Set

from . import paths, sensitive, skills, syncstate

OWN = ("learned", "edited")


def own_skills() -> Dict[str, Path]:
    """Name -> store folder for every skill that is eki's own (the shared rule)."""
    where = skills.sources()
    base = syncstate.baseline("skills")
    found: Dict[str, Path] = {}
    for folder in sorted(p for p in paths.skills().iterdir() if p.is_dir()):
        if not (folder / "SKILL.md").is_file():
            continue
        source = where.get(folder.name)
        if source in OWN:
            found[folder.name] = folder
        elif source == "claude":
            seen = base.get(folder.name)
            hash_then = seen.get("hash") if isinstance(seen, dict) else None
            if syncstate.tree_hash(folder) != hash_then:
                found[folder.name] = folder
    return found


def _held_back(folder: Path, hidden: Set[str]) -> List[str]:
    """The sensitive files inside `folder`, as paths relative to it."""
    root = sensitive.norm(str(folder))
    return sorted(os.path.relpath(p, root) for p in hidden if p.startswith(root + os.sep))


def _copy(src: Path, tmp: Path, hidden: Set[str]) -> None:
    def ignore(where: str, names: List[str]) -> Set[str]:
        return {n for n in names if n in syncstate.IGNORED
                or sensitive.norm(os.path.join(where, n)) in hidden}
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=ignore)


def _mirror(src: Path, dest: Path, hidden: Set[str]) -> bool:
    """Copy src over dest, swapping the folder in whole so the client never
    reads half a skill. Whether dest changed."""
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    _copy(src, tmp, hidden)
    if syncstate.tree_hash(tmp) == syncstate.tree_hash(dest):
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    old = dest.with_name(f".{dest.name}.old-{os.getpid()}")
    if dest.exists():
        os.replace(dest, old)
    os.replace(tmp, dest)
    shutil.rmtree(old, ignore_errors=True)
    return True


def tick(conn: sqlite3.Connection) -> List[str]:
    out = syncstate.outbox() / "skills"
    out.mkdir(exist_ok=True)
    said: List[str] = []
    hidden = sensitive.paths(conn)
    for name, folder in own_skills().items():
        dest = out / name
        held = _held_back(folder, hidden)
        if not held and syncstate.tree_hash(dest) == syncstate.tree_hash(folder):
            continue
        if not _mirror(folder, dest, hidden):
            continue
        said.append(f"skill {name} written to the outbox for Claude")
        if held:
            said.append(f"skill {name}: left out {len(held)} sensitive "
                        f"picture{'s' if len(held) != 1 else ''} ({', '.join(held)})")
    count = sum(1 for p in out.iterdir() if p.is_dir() and not p.name.startswith("."))
    syncstate.record("skills-out", count=count)
    return said
