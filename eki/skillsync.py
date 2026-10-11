"""Skills, Claude client -> eki: every skill Claude synced to ~/.claude/skills/synced comes into the store.

An imported skill has source `claude` and a baseline (`sync/skills.json`,
`{name: {hash, from}}`, the store copy's tree hash right after import). A
store skill is eki's own when its source is `learned` or `edited`, or it is
`claude` and its tree hash no longer matches the baseline; eki's own skills
are never overwritten or removed here. A client skill whose name is held by
anything else (eki's own, a folder, a git URL, no source) is a clash: left
alone and shown. A pass with nothing changed only hashes, never copies.

Order of writes keeps a restart safe: the copy is made aside, then baseline
and source are written, then it is moved into place — a crash in between
leaves a `claude` source with no folder, which the next pass imports again.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from . import paths, skills, syncstate

SOURCE = "claude"
OWN = ("learned", "edited")


def _why_own(name: str, source: Optional[str], base: Dict) -> Optional[str]:
    """Why the store's `name` is eki's own (or not Claude's to touch); None when the import may manage it."""
    if source == SOURCE:
        entry = base.get(name)
        expected = entry.get("hash") if isinstance(entry, dict) else None
        if syncstate.tree_hash(paths.skills() / name) != expected:
            return "edited in eki since the import"
        return None
    if source == "learned":
        return "learned in eki"
    if source == "edited":
        return "edited in eki"
    if not source:
        return "added in eki, source unknown"
    return f"added from {source}"


def _install(name: str, src: Path, base: Dict) -> None:
    """Copy `src` aside, record baseline and source, then put it in the store as `name`."""
    tmp = paths.home() / "tmp"
    tmp.mkdir(exist_ok=True)
    aside = Path(tempfile.mkdtemp(prefix="skillsync-", dir=tmp))
    try:
        copy = aside / name
        shutil.copytree(src, copy, ignore=shutil.ignore_patterns(*syncstate.IGNORED))
        base[name] = {"hash": syncstate.tree_hash(copy), "from": str(src)}
        syncstate.set_baseline("skills", base)
        skills.set_source(name, SOURCE)
        dest = paths.skills() / name
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)
        copy.rename(dest)
    finally:
        shutil.rmtree(aside, ignore_errors=True)


def _drop(name: str, base: Dict) -> None:
    dest = paths.skills() / name
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    skills.set_source(name, None)
    if base.pop(name, None) is not None:
        syncstate.set_baseline("skills", base)


def tick(conn: sqlite3.Connection) -> List[str]:
    client = syncstate.client_skills()
    where = skills.sources()
    base = syncstate.baseline("skills")
    store = paths.skills()
    said: List[str] = []
    clashes: List[str] = []

    for name, src in sorted(client.items()):
        dest = store / name
        source = where.get(name)
        if not (dest.exists() or dest.is_symlink()):
            if source in (None, SOURCE):
                _install(name, src, base)
                said.append(f"skills: imported {name} from Claude")
            else:                                    # a source with no folder: the person's, not ours
                clashes.append(f"{name}: kept eki's copy ({_why_own(name, source, base)})")
            continue
        why = _why_own(name, source, base)
        if why:
            clashes.append(f"{name}: kept eki's copy ({why})")
            continue
        if syncstate.tree_hash(src) != base[name]["hash"]:
            _install(name, src, base)
            said.append(f"skills: refreshed {name} from Claude")

    for name, source in sorted(where.items()):
        if source != SOURCE or name in client:
            continue
        dest = store / name
        if (dest.exists() or dest.is_symlink()) and _why_own(name, source, base):
            continue                                 # eki's own now: kept, and no longer Claude's to show
        _drop(name, base)
        said.append(f"skills: removed {name} (gone from Claude)")

    if said:
        skills.sync_codex()
    count = sum(1 for s in skills.sources().values() if s == SOURCE)
    syncstate.record("skills-in", count=count, clashes=clashes)
    return said
