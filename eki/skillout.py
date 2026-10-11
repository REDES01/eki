"""Skills, eki -> client: eki's own skills are mirrored to the outbox.

A store skill is eki's own when `skill-sources.json` gives it source
'learned' or 'edited', or source 'claude' with a store copy whose tree hash
differs from the import baseline. Each one is copied, whole folder, to
`sync/outbox/skills/<name>/` when the copy there is missing or differs; the
client task proposes it to the owner and consumes it. eki never deletes an
outbox entry and writes nothing anywhere else.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List

from . import paths, skills, syncstate

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


def _mirror(src: Path, dest: Path) -> None:
    """Copy src over dest, swapping the folder in whole so the client never reads half a skill."""
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(*syncstate.IGNORED))
    old = dest.with_name(f".{dest.name}.old-{os.getpid()}")
    if dest.exists():
        os.replace(dest, old)
    os.replace(tmp, dest)
    shutil.rmtree(old, ignore_errors=True)


def tick(conn: sqlite3.Connection) -> List[str]:
    out = syncstate.outbox() / "skills"
    out.mkdir(exist_ok=True)
    said: List[str] = []
    for name, folder in own_skills().items():
        dest = out / name
        if syncstate.tree_hash(dest) == syncstate.tree_hash(folder):
            continue
        _mirror(folder, dest)
        said.append(f"skill {name} written to the outbox for Claude")
    count = sum(1 for p in out.iterdir() if p.is_dir() and not p.name.startswith("."))
    syncstate.record("skills-out", count=count)
    return said
