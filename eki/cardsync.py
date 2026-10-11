"""Project skills cards: refresh the entries that came from the Claude client.

A project's skills card (eki/components/skillcard.py) may hold copies of the
owner's Claude skills — holo-cards keeps game-design-principles,
holo-card-maker and comfyui-local-api by hand. Each housekeeping pass, every
card skill whose name is also a client skill (syncstate.client_skills) is
compared with the client copy:

- with a baseline (the card copy's tree hash right after the last refresh) and
  the card unchanged since, a changed client copy replaces it;
- with a baseline and the card edited, the project's copy is kept: a clash;
- without a baseline (a hand copy): an equal copy only gets its baseline; an
  older SKILL.md is backed up under backups()/cards/<project>/ and replaced; a
  newer one is kept as edited: a clash.

Nothing is ever added to or deleted from a card — a skill gone from the client
stays, it is versioned with the project — and eki never commits there. The
clashes join the 'skills-in' direction's, each starting with '<project>/'.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import projects, syncstate
from .components import skillcard

log = logging.getLogger("eki.cardsync")

BASELINE = "cards"
#: the card clashes this module last merged into 'skills-in', so the next pass can take them out
SHOWN = "card-clashes"


def _key(card: Path, name: str) -> str:
    return f"{card}/{name}"


def _label(project: sqlite3.Row) -> str:
    return (project["name"] or Path(project["path"]).name).replace("/", "-")


def _mtime(folder: Path) -> float:
    try:
        return (folder / "SKILL.md").stat().st_mtime
    except OSError:
        return 0.0


def _replace(dest: Path, src: Path) -> None:
    """Swap `dest` for a copy of `src`; the hidden temp names are skipped by the card readers."""
    stamp = f"{os.getpid()}-{time.time_ns()}"
    fresh = dest.with_name(f".{dest.name}.new-{stamp}")
    old = dest.with_name(f".{dest.name}.old-{stamp}")
    shutil.copytree(src, fresh, symlinks=True)
    os.replace(dest, old)
    os.replace(fresh, dest)
    shutil.rmtree(old, ignore_errors=True)


def _backup(dest: Path, project: str, name: str) -> Path:
    where = syncstate.backups() / "cards" / project
    where.mkdir(parents=True, exist_ok=True)
    target = where / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    n = 1
    while target.exists():
        n += 1
        target = where / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}-{n}"
    shutil.copytree(dest, target, symlinks=True)
    return target


def _one(dest: Path, src: Path, label: str, base: Optional[Dict]) -> Tuple[Optional[Dict], str, str]:
    """(new baseline or None to leave it, refreshed line, clash) for one card skill."""
    name = dest.name
    have, want = syncstate.tree_hash(dest), syncstate.tree_hash(src)
    entry = {"hash": want, "from": str(src)}
    if have == want:                                     # already the client's copy
        return (None if base == entry else entry), "", ""
    if base and base.get("hash"):
        if have != base["hash"]:
            return None, "", f"{label}/{name}: kept the project's copy (edited in the project)"
        _replace(dest, src)
        return entry, f"{label}/{name}: refreshed from the Claude client", ""
    if _mtime(dest) < _mtime(src):
        saved = _backup(dest, label, name)
        _replace(dest, src)
        return entry, f"{label}/{name}: refreshed from the Claude client (old copy in {saved})", ""
    return None, "", f"{label}/{name}: kept the project's copy (newer than the Claude client's)"


def _merge(clashes: List[str], labels: List[str]) -> None:
    """Put this pass's card clashes into 'skills-in' in place of the last pass's."""
    earlier = set(syncstate.baseline(SHOWN).get("clashes") or [])
    prefixes = tuple(f"{l}/" for l in labels)
    now = syncstate.status()["skills-in"]
    theirs = [c for c in now.get("clashes") or []
              if c not in earlier and not (prefixes and c.startswith(prefixes))]
    merged = theirs + clashes
    if merged != list(now.get("clashes") or []):
        syncstate.record("skills-in", count=now.get("count") or 0, clashes=merged, note=now.get("note") or "")
    if sorted(earlier) != sorted(clashes):
        syncstate.set_baseline(SHOWN, {"clashes": clashes})


def tick(conn: sqlite3.Connection) -> List[str]:
    client = syncstate.client_skills()
    said: List[str] = []
    clashes: List[str] = []
    labels: List[str] = []
    bases = syncstate.baseline(BASELINE)
    changed = False
    for project in projects.all(conn):
        if not project["path"] or not Path(project["path"]).is_dir():
            continue
        label = _label(project)
        labels.append(label)
        if not client:
            continue
        try:
            cards = skillcard.cards_for(project["path"], conn)
        except Exception:                                # noqa: BLE001 — one project never stops the rest
            log.exception("skills cards of %s unreadable", label)
            continue
        for card in cards:
            try:
                subs = sorted(p for p in Path(card).iterdir()
                              if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))
            except OSError:
                continue
            for dest in subs:
                src = client.get(dest.name)
                if src is None:
                    continue
                key = _key(Path(card), dest.name)
                try:
                    entry, line, clash = _one(dest, src, label, bases.get(key))
                except OSError:
                    log.exception("refreshing %s from %s failed", dest, src)
                    continue
                if entry is not None:
                    bases[key] = entry
                    changed = True
                if line:
                    said.append(line)
                if clash:
                    clashes.append(clash)
    if changed:
        syncstate.set_baseline(BASELINE, bases)
    _merge(clashes, labels)
    return said
