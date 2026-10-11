"""The root of the Claude-client sync: its folders, what each direction last did, and baselines.

Everything lives in files under `~/.eki/sync` — nothing in memory — so a
restart loses nothing. The client side (a scheduled Claude task set up outside
eki) writes `sync/claude-memory/` and reads `sync/outbox/`; eki only reads the
mirror and writes the outbox. `~/.claude` is the owner's and is never created
here. Each direction is a module of its own with a `tick(conn)`; `tick` below
runs the ones that exist, each on its own, from housekeeping.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List

from . import observe, paths

log = logging.getLogger("eki.syncstate")

DIRECTIONS = ("skills-in", "skills-out", "memory-in", "memory-out", "instructions")
#: the direction modules, run in this order each housekeeping pass
MODULES = ("skillsync", "skillout", "cardsync", "instrsync", "memnotes", "memoutbox")
IGNORED = {".DS_Store"}


def root() -> Path:
    p = paths.home() / "sync"
    p.mkdir(exist_ok=True)
    return p


def memory_dir() -> Path:
    """The client task's mirror of Claude's memory; eki never creates it."""
    return root() / "claude-memory"


def outbox() -> Path:
    p = root() / "outbox"
    p.mkdir(exist_ok=True)
    return p


def backups() -> Path:
    p = root() / "backups"
    p.mkdir(exist_ok=True)
    return p


def claude_dir() -> Path:
    """The owner's ~/.claude; eki never creates it."""
    return paths.user_home() / ".claude"


def agents_md() -> Path:
    """eki's canonical instructions for every run."""
    return paths.home() / "AGENTS.md"


def client_skills() -> Dict[str, Path]:
    """Skill name -> folder for every skill Claude synced to disk; the first account (sorted) wins a name."""
    synced = claude_dir() / "skills" / "synced"
    found: Dict[str, Path] = {}
    try:
        accounts = sorted(p for p in synced.iterdir() if p.is_dir())
    except OSError:
        return {}
    for account in accounts:
        try:
            skills = sorted(p for p in account.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in skills:
            if folder.name not in found and (folder / "SKILL.md").is_file():
                found[folder.name] = folder
    return found


def tree_hash(folder: Path) -> str:
    """sha1 over every file's relative path and bytes, sorted; '' when the folder is missing."""
    if not folder.is_dir():
        return ""
    h = hashlib.sha1()
    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.name not in IGNORED),
                   key=lambda p: p.relative_to(folder).as_posix())
    for p in files:
        rel = p.relative_to(folder).as_posix().encode()
        h.update(len(rel).to_bytes(4, "big") + rel)
        try:
            data = p.read_bytes()
        except OSError:
            data = b""
        h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def _read(path: Path) -> Dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: Dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def baseline(name: str) -> Dict:
    return _read(root() / f"{name}.json")


def set_baseline(name: str, data: Dict) -> None:
    _write(root() / f"{name}.json", data)


def record(direction: str, *, count: int, clashes: Iterable[str] = (), note: str = "") -> None:
    """What a direction did on its last run; the other directions are left as they were."""
    path = root() / "state.json"
    state = _read(path)
    state[direction] = {"at": time.time(), "count": count, "clashes": list(clashes), "note": note}
    _write(path, state)


def status() -> Dict[str, Dict]:
    state = _read(root() / "state.json")
    empty = {"at": None, "count": 0, "clashes": [], "note": ""}
    return {d: state.get(d) if isinstance(state.get(d), dict) else dict(empty) for d in DIRECTIONS}


def tick(conn: sqlite3.Connection) -> List[str]:
    """Run each direction module that exists; one that raises is a fault and the rest still run."""
    said: List[str] = []
    for name in MODULES:
        full = f"eki.{name}"
        try:
            mod = importlib.import_module(full)
        except Exception as e:                           # noqa: BLE001
            if isinstance(e, ModuleNotFoundError) and e.name == full:
                continue                                 # not built yet
            log.exception("sync module %s did not import", name)
            observe.fault(conn, f"sync.{name}")
            continue
        try:
            said.extend(mod.tick(conn) or [])
        except Exception:                                # noqa: BLE001 — one failing never stops the others
            log.exception("sync step %s failed", name)
            observe.fault(conn, f"sync.{name}")
    return said
