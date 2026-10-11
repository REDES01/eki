"""A project's loaded cards, kept for the life of the process.

Scanning root/components loads and validates every card; the project page and every run
do it often. Here each card folder is loaded once and answered from memory until its key
changes: the folder's mtime_ns, its component.json's (mtime_ns, size) and, for a pool card,
the pool component's component.json (mtime_ns, size). The components/ folder's own mtime_ns
says when a card folder was added or removed. Callers get copies, so changing a returned
spec never changes what is kept.
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

_lock = threading.Lock()
#: (components folder, pool folder) -> {'dir': mtime_ns, 'names': [...], 'cards': {name: (key, pool, result)}}
_cache: Dict[Tuple[str, str], dict] = {}


def _stat(p: Path) -> Optional[Tuple[int, int]]:
    try:
        s = p.stat()
    except OSError:
        return None
    return s.st_mtime_ns, s.st_size


def _key(d: Path) -> Optional[tuple]:
    folder = _stat(d)
    return None if folder is None else (folder[0], _stat(d / 'component.json'))


def _copy(c):
    from . import Component
    return Component(name=c.name, kind=c.kind, folder=c.folder, spec=copy.deepcopy(c.spec),
                     scope=c.scope, card=c.card)


def _load(d: Path) -> Tuple[Optional[str], Union[object, str]]:
    """(pool name when `d` is a pool card, the Component or the reason it's broken)."""
    from .. import components as C
    pool = None
    try:
        ref = C._ref(d)
        pool = ref.get('pool') if ref is not None and isinstance(ref.get('pool'), str) else None
        return pool, (C.load_card(d, ref) if ref is not None else C.load(d, 'project'))
    except Exception as e:   # a broken pool card still keys on its pool component: adding or fixing it clears this
        return pool, str(e)


def scan(base: Path, found: dict, errors: List[Tuple[str, str]]) -> None:
    """Like a project scan of `base`: fills `found` (name -> Component copy) and `errors`."""
    from .. import components as C
    pool_dir = C.pool_dir()
    ck = (str(base), str(pool_dir))
    dir_stat = _stat(base)
    if dir_stat is None or not base.is_dir():
        with _lock:
            _cache.pop(ck, None)
        return
    with _lock:
        held = _cache.get(ck) or {}
        cards = dict(held.get('cards') or {})
    names = held.get('names') if held.get('dir') == dir_stat[0] else None
    if names is None:
        try:
            names = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith('.'))
        except OSError as e:
            errors.append((str(base), f'unreadable: {e}'))
            return
    fresh: Dict[str, tuple] = {}
    for name in names:
        d = base / name
        key = _key(d)
        if key is None:      # gone since the listing
            continue
        was = cards.get(name)
        if was is not None and was[1] is not None:
            key = key + (_stat(pool_dir / was[1] / 'component.json'),)
        if was is not None and was[0] == key:
            fresh[name] = was
            continue
        pool, result = _load(d)
        if pool is not None:
            key = _key(d) + (_stat(pool_dir / pool / 'component.json'),)
        fresh[name] = (key, pool, result)
    with _lock:
        _cache[ck] = {'dir': dir_stat[0], 'names': list(names), 'cards': fresh}
    for name in names:
        if name not in fresh:
            continue
        result = fresh[name][2]
        if isinstance(result, str):
            errors.append((str(base / name), result))
        else:
            found[name] = _copy(result)


def clear() -> None:
    with _lock:
        _cache.clear()
