"""The media pool's undo: copies of its files in a backup folder or drive.

The pool is kept out of git, so it has no history. An assets card's
`"backup"` names an absolute folder (or a drive's path); housekeeping copies
every file that is new or changed there (`tick`, at most every
assets.POOL_EVERY seconds, on a thread of its own). A project pool goes to
`<backup>/<project folder name>/<pool path>/`, a pool component's to
`<backup>/<component name>/`. Each copy keeps its mtime and lands by rename,
so the newest version replaces the older one; nothing is ever deleted from
the backup, so a file removed from the pool is still there. Symlinks are not
followed. A target whose folder and parent are both missing (an unplugged
drive) copies nothing.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import Component, assets

log = logging.getLogger("eki.assetsbackup")

#: mtimes closer than this count as the same (exFAT keeps 10 ms)
MTIME_SLACK = 0.01


def target(comp: Component) -> Path:
    """Where this component's pool is copied; ValueError when no backup is set."""
    b = assets.backup_target(comp)
    if not b:
        raise ValueError(f'{comp.name} has no backup set')
    if comp.scope == 'project':
        return Path(b) / assets.root_of(comp).name / comp.spec['path']
    return Path(b) / comp.name


def _changed(src: os.stat_result, dest: Path) -> bool:
    try:
        st = dest.lstat()
    except OSError:
        return True
    return st.st_size != src.st_size or abs(st.st_mtime - src.st_mtime) > MTIME_SLACK


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f'.{dest.name}.', suffix='.ekibak')
    os.close(fd)
    try:
        shutil.copyfile(src, tmp, follow_symlinks=False)
        shutil.copystat(src, tmp, follow_symlinks=False)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup(comp: Component) -> int:
    """Copy each new or changed file of the pool to its backup; how many were copied.
    OSError when the backup folder and its parent are both missing."""
    root = Path(assets.backup_target(comp))
    if not root.exists() and not root.parent.exists():
        raise OSError(f'backup target not reachable: {root}')
    src_root = assets.root_of(comp) / comp.spec['path']
    if not src_root.is_dir() or src_root.is_symlink():
        return 0
    dest_root = target(comp)
    n = 0
    for dirpath, _, files in os.walk(src_root):          # never into a linked folder
        for f in files:
            src = Path(dirpath) / f
            try:
                st = src.lstat()
                if not os.path.isfile(src) or src.is_symlink():
                    continue
                dest = dest_root / src.relative_to(src_root)
                if _changed(st, dest):
                    _copy(src, dest)
                    n += 1
            except FileNotFoundError:                    # gone while walking
                continue
    return n


def _all(roots: List[Path]) -> List[Component]:
    """Every assets card of the roots and pool assets component with a backup set, once each."""
    out, seen = [], set()
    for comp in assets._pools(roots):
        try:
            if not assets.backup_target(comp):
                continue
            key = (str(assets.root_of(comp) / comp.spec['path']), str(target(comp)))
        except (KeyError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            out.append(comp)
    return out


def run(roots: List[Path]) -> int:
    """Back up every pool that has a backup set; files copied in all. Failures are logged."""
    total = 0
    for comp in _all(roots):
        try:
            got = backup(comp)
            total += got
            if got:
                log.info("backed up %d file%s of %s to %s", got, '' if got == 1 else 's', comp.name, target(comp))
        except Exception as e:                           # noqa: BLE001 — one pool never stops the others
            log.info("pool %s not backed up: %s", comp.name, e)
    return total


_last = [0.0]
_running = threading.Lock()
#: the backup thread last started (tests join it)
_thread: List[Optional[threading.Thread]] = [None]


def _run_thread(roots: List[Path]) -> None:
    try:
        run(roots)
    except Exception as e:                               # noqa: BLE001 — a backup is never worth a fault
        log.info("pool backups not run: %s", e)
    finally:
        _running.release()


def tick(conn: sqlite3.Connection) -> List[str]:
    """Housekeeping: one thread backing up the pools that have a backup set, at most every
    assets.POOL_EVERY s, never two at once, never waiting."""
    if time.time() - _last[0] < assets.POOL_EVERY:
        return []
    if not _running.acquire(blocking=False):
        return []
    _last[0] = time.time()
    try:
        roots = [Path(r['path']) for r in conn.execute('SELECT path FROM projects')]
        roots = [r for r in roots if r.is_dir()]
        t = threading.Thread(target=_run_thread, args=(roots,), name='eki-poolbackup', daemon=True)
        t.start()
    except BaseException:
        _running.release()
        raise
    _thread[0] = t
    return []
