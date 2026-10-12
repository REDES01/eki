"""The `assets` kind: a folder eki manages outside git, seen by every worktree.

`"path"` is relative to the project root (a pool component's to its own
folder). That folder in the project itself is the pool: created when missing,
never versioned like code. Every worktree of the project sees it at the same
relative path through a symlink — never a copy or hardlink — so anything
written there lands in the pool at once. Adding the card git-ignores the pool
and untracks what was tracked inside it (eki/components/poolignore.py), so it
is linked; should it be tracked again, the card says why it isn't. The pool has
no version history: `"backup"` (an absolute folder or drive path, '' for none)
is where housekeeping copies new and changed files, never deleting one
(eki/components/assetsbackup.py). Calls: `ls [sub]`, `info <path>`,
`rm <path>` (to the Trash, never a real delete).

A pool's size is never walked during a request: housekeeping measures every
pool at most every POOL_EVERY seconds on a thread of its own (`tick`) and
writes it to paths.home()/poolsizes.json; `stored_size`, `size_text` and
`notes` read that.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .. import paths
from . import Call, Component, Result, find, own, project_root

log = logging.getLogger("eki.assets")

ABOUT = "the project's pictures, sounds and other big files, kept in one place every agent sees"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Media pool"
EXPLAIN = ("The project's pictures, sounds and other big files, kept in one place outside the code history. "
           "Every agent working on the project sees the same pool, so a picture made by one is there for the "
           "next. Tools that make media save into it, and the game reads from it.")
USAGE = 'use: ls [sub] | info <path> | rm <path>'


def validate(spec: dict, folder: Path) -> List[str]:
    p = spec.get('path')
    if not isinstance(p, str) or not p.strip():
        return ['assets need "path": a folder relative to the project']
    if Path(p).is_absolute() or '..' in Path(p).parts or Path(p) == Path('.'):
        return [f'path {p!r} must be a folder below the project, with no ".."']
    b = spec.get('backup', '')
    if not isinstance(b, str) or (b and not Path(b).is_absolute()):
        return ['backup must be an absolute folder path']
    return []


#: the first note of a card without a backup
NO_HISTORY = ("No version history: the pool is kept out of git, so a changed or deleted file "
              "can't be undone — set a backup folder to keep copies.")


def backup_target(comp: Component) -> str:
    """The card's backup folder; '' when none is set."""
    b = comp.spec.get('backup') or ''
    return b if isinstance(b, str) else ''


def root_of(comp: Component) -> Path:
    """The project root of a project component, the component's own folder for a pool one."""
    return comp.folder.parent.parent if comp.scope == 'project' else comp.folder


def pool(comp: Component) -> Path:
    """The managed folder itself, created when missing."""
    p = root_of(comp) / comp.spec['path']
    p.mkdir(parents=True, exist_ok=True)
    return p


def _inside(comp: Component, rel: str) -> Path:
    """`rel` resolved inside the pool; ValueError on an escape."""
    if Path(rel).is_absolute() or '..' in Path(rel).parts:
        raise ValueError(f'{rel!r} must be a path inside the pool, with no ".."')
    base = pool(comp).resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f'{rel!r} leads outside the pool')
    return target


def _size(p: Path) -> int:
    if p.is_symlink() or not p.is_dir():
        return p.lstat().st_size
    total = 0
    for dirpath, _, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n} B'


def _ls(comp: Component, sub: str) -> Result:
    where = _inside(comp, sub)
    if not where.is_dir():
        return Result(error=f'no folder {sub} in {comp.name}')
    lines = []
    for p in sorted(where.iterdir(), key=lambda q: q.name):
        if p.is_dir() and not p.is_symlink():
            lines.append(f'{p.name}/\t{_human(_size(p))}')
        else:
            lines.append(f'{p.name}\t{_human(_size(p))}')
    return Result(text='\n'.join(lines) or '(empty)', paths=[str(where)])


def _info(comp: Component, rel: str) -> Result:
    p = _inside(comp, rel)
    if not p.exists() and not p.is_symlink():
        return Result(error=f'no {rel} in {comp.name}')
    st = p.lstat()
    kind = 'link' if stat.S_ISLNK(st.st_mode) else 'folder' if p.is_dir() else 'file'
    when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
    return Result(text=f'{rel}\n{kind}\n{_human(_size(p))} ({_size(p)} bytes)\nmodified {when}',
                  paths=[str(p)])


def trash() -> Path:
    return paths.user_home() / '.Trash'


def _rm(comp: Component, rel: str) -> Result:
    p = _inside(comp, rel)
    if p == pool(comp).resolve():
        return Result(error='refusing to remove the whole pool')
    if not p.exists() and not p.is_symlink():
        return Result(error=f'no {rel} in {comp.name}')
    bin_ = trash()
    bin_.mkdir(parents=True, exist_ok=True)
    dest = bin_ / p.name
    n = 1
    while dest.exists() or dest.is_symlink():
        dest = bin_ / f'{p.stem} {time.strftime("%H.%M.%S")}{"" if n == 1 else f" {n}"}{p.suffix}'
        n += 1
    shutil.move(str(p), str(dest))
    return Result(text=f'moved {rel} to the Trash: {dest}', paths=[str(dest)])


def call(comp: Component, c: Call) -> Result:
    args = list(c.args)
    verb = args.pop(0) if args else 'ls'
    try:
        if verb == 'ls' and len(args) <= 1:
            return _ls(comp, args[0] if args else '.')
        if verb in ('info', 'rm') and len(args) == 1:
            return (_info if verb == 'info' else _rm)(comp, args[0])
    except (ValueError, OSError) as e:
        return Result(error=str(e))
    return Result(error=f'{comp.name}: {USAGE}')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def _git(root: Path, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(['git', '-C', str(root), *args],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def problems(comp: Component) -> List[str]:
    """Why this project pool can't be linked into worktrees: not git-ignored as a
    whole, or files inside it tracked by git. Empty when it can (or there's no repo)."""
    if comp.scope != 'project':
        return []
    root = root_of(comp)
    rel = comp.spec['path'].rstrip('/')
    out = []
    ign = _git(root, 'check-ignore', '-q', '--no-index', rel + '/')   # a folder, even before it exists
    if ign is not None and ign.returncode == 1:         # 0 ignored, 128 no repo: both fine
        out.append(f'{rel}/ is not git-ignored in {root}; not linked into worktrees')
    ls = _git(root, 'ls-files', '--', rel)
    tracked = ls.stdout.splitlines() if ls is not None and ls.returncode == 0 else []
    if tracked:
        n = len(tracked)
        out.append(f'{rel}/ has {n} tracked file{"" if n == 1 else "s"} in git '
                   f'(e.g. {tracked[0]}); not linked into worktrees: untrack them first')
    return out


def pool_size(comp: Component) -> int:
    """Bytes in the pool, following no symlinks; 0 when it is missing."""
    p = root_of(comp) / comp.spec['path']
    try:
        return _size(p) if p.exists() or p.is_symlink() else 0
    except OSError:
        return 0


def sizes_file() -> Path:
    return paths.home() / 'poolsizes.json'


def _key(comp: Component) -> str:
    return str((root_of(comp) / comp.spec['path']).resolve())


def _stored() -> Dict[str, dict]:
    try:
        got = json.loads(sizes_file().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def stored_size(comp: Component) -> Optional[int]:
    """The pool's bytes as housekeeping last measured them; None when never measured."""
    try:
        n = _stored().get(_key(comp), {}).get('bytes')
    except (AttributeError, KeyError, TypeError, OSError):
        return None
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def size_text(comp: Component) -> str:
    """The stored pool size in words; '' when never measured or not an assets card."""
    if comp.kind != 'assets':
        return ''
    n = stored_size(comp)
    return '' if n is None else _human(n)


def _write(sizes: Dict[str, dict]) -> None:
    f = sizes_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix='.poolsizes.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(sizes, out, indent=1, sort_keys=True)
        os.replace(tmp, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: how often housekeeping measures the pools
POOL_EVERY = 300.0
_last_measure = [0.0]
_measuring = threading.Lock()


def _pools(roots: List[Path]) -> List[Component]:
    """Every assets card of the given project roots, and every assets component in the pool."""
    out: List[Component] = []
    for root in roots:
        out += [c for c in own(root)[0].values() if c.kind == 'assets']
    out += [c for c in find(None)[0].values() if c.kind == 'assets']
    return out


def measure(roots: List[Path]) -> Dict[str, dict]:
    """Walk each pool once and write all the sizes; what was written."""
    sizes: Dict[str, dict] = {}
    for comp in _pools(roots):
        try:
            key = _key(comp)
            if key not in sizes:
                sizes[key] = {'bytes': pool_size(comp), 'at': time.time()}
        except (OSError, KeyError, TypeError, ValueError):
            continue
    _write(sizes)
    return sizes


def _measure_thread(roots: List[Path]) -> None:
    try:
        measure(roots)
    except Exception as e:                               # noqa: BLE001 — a size is never worth a fault
        log.info("pool sizes not measured: %s", e)
    finally:
        _measuring.release()


#: the measuring thread last started (tests join it)
_thread: List[Optional[threading.Thread]] = [None]


def tick(conn: sqlite3.Connection) -> List[str]:
    """Housekeeping: start one thread measuring every pool, at most every POOL_EVERY s, never waiting."""
    if time.time() - _last_measure[0] < POOL_EVERY:
        return []
    if not _measuring.acquire(blocking=False):           # one still running: never two at once
        return []
    _last_measure[0] = time.time()
    try:
        roots = [Path(r['path']) for r in conn.execute('SELECT path FROM projects')]
        roots = [r for r in roots if r.is_dir()]
        t = threading.Thread(target=_measure_thread, args=(roots,), name='eki-poolsizes', daemon=True)
        t.start()
    except BaseException:
        _measuring.release()
        raise
    _thread[0] = t
    return [f'pool sizes: measuring {len(roots)} project{"" if len(roots) == 1 else "s"} and the pool']


def notes(comp: Component) -> List[str]:
    text = size_text(comp)
    b = backup_target(comp)
    history = f'No version history in git; backed up to {b}' if b else NO_HISTORY
    return [history] + problems(comp) + [f'pool: {text}' if text else 'pool: not measured yet']


def linked(worktree, conn=None) -> List[str]:
    """The relative paths in `worktree` that are links to its project's asset pools."""
    out = []
    found, _ = find(worktree, conn)
    for comp in found.values():
        if comp.kind == 'assets' and comp.scope == 'project':
            p = Path(worktree) / comp.spec['path']
            if p.is_symlink() and os.readlink(p) == str(root_of(comp) / comp.spec['path']):
                out.append(comp.spec['path'])
    return out


def link(worktree: Path, conn=None) -> List[str]:
    """Make each project asset pool reachable from `worktree` a symlink at the same
    relative path; what was linked, and what was left alone (and why)."""
    worktree = Path(worktree)
    root: Optional[Path] = project_root(worktree, conn)
    if root is None or root.resolve() == worktree.resolve():
        return []
    done = []
    found, _ = find(worktree, conn)
    for comp in found.values():
        if comp.kind != 'assets' or comp.scope != 'project':
            continue
        rel = comp.spec['path']
        why = problems(comp)
        if why:
            done += [f'{comp.name}: {w}' for w in why]
            continue
        src = pool(comp)
        dest = worktree / rel
        if dest.is_symlink() and os.readlink(dest) == str(src):
            continue
        if dest.exists() or dest.is_symlink():
            done.append(f'{comp.name}: {dest} already exists and is not a link to {src}; left alone')
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, dest)
        done.append(f'{comp.name}: {dest} -> {src}')
    return done
