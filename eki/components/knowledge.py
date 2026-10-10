"""The `knowledge` kind: files every agent of the project reads — decisions, design rules,
contracts, notes. Read or opened, never called with `eki use`.

`"files"` are paths relative to the project root (globs and folders allowed, no absolute
path, no ".."); `"read"` is "always" (read first) or "when-relevant" (look there when the
`about` line fits). A pattern that matches nothing is shown missing and left out of the brief.
"""
from __future__ import annotations

import glob
import sqlite3
from pathlib import Path
from typing import List, Optional

from . import Call, Component, Result

ABOUT = "files every agent of the project reads: always, or when relevant"
READ = ('always', 'when-relevant')


def _bad_pattern(p) -> str:
    if not isinstance(p, str) or not p.strip():
        return 'files must be non-empty paths'
    if Path(p).is_absolute() or p.startswith('~'):
        return f'files {p!r} must be relative to the project'
    if '..' in Path(p).parts:
        return f'files {p!r} must stay inside the project, with no ".."'
    return ''


def validate(spec: dict, folder: Path) -> List[str]:
    errs = []
    fs = spec.get('files')
    if not isinstance(fs, list) or not fs:
        errs.append('files must be a non-empty list of paths in the project (globs and folders allowed)')
    else:
        errs += [e for e in (_bad_pattern(p) for p in fs) if e][:1]
    if spec.get('read') not in READ:
        errs.append(f'read must be one of {", ".join(READ)} (got {spec.get("read")!r})')
    return errs


def root_of(comp: Component) -> Path:
    return (comp.card or comp.folder).parent.parent


def _entry(pattern: str, root: Path, p: Optional[Path]) -> dict:
    if p is None:
        return {'pattern': pattern, 'path': pattern, 'abs': str(root / pattern), 'name': Path(pattern).name,
                'size': None, 'changed': None, 'author': '', 'missing': True}
    try:
        st = p.stat()
        size, changed = st.st_size, st.st_mtime
    except OSError:
        size, changed = None, None
    return {'pattern': pattern, 'path': str(p.relative_to(root)), 'abs': str(p), 'name': p.name,
            'size': size, 'changed': changed, 'author': '', 'missing': False}


def _inside(root: Path, p: Path) -> Optional[Path]:
    try:
        r = p.resolve()
    except OSError:
        return None
    return r if r == root or root in r.parents else None


def _expand(root: Path, pattern: str) -> List[Path]:
    if _bad_pattern(pattern):
        return []
    hits = sorted(glob.glob(str(root / pattern), recursive=True)) if glob.has_magic(pattern) else [str(root / pattern)]
    out: List[Path] = []
    for h in hits:
        p = Path(h)
        if p.is_dir():
            out += sorted(f for f in p.rglob('*') if f.is_file()
                          and not any(part.startswith('.') for part in f.relative_to(p).parts))
        elif p.is_file():
            out.append(p)
    seen, kept = set(), []
    for p in out:
        r = _inside(root, p)
        if r is not None and r not in seen:
            seen.add(r)
            kept.append(r)
    return kept


def files(comp: Component) -> List[dict]:
    """One dict per file the card names; a pattern matching nothing gives one missing entry."""
    root = root_of(comp).resolve()
    out: List[dict] = []
    for pattern in comp.spec.get('files') or []:
        found = _expand(root, pattern)
        out += [_entry(pattern, root, p) for p in found] if found else [_entry(pattern, root, None)]
    return out


def notes(comp: Component) -> List[str]:
    fs = files(comp)
    gone = [f['path'] for f in fs if f['missing']]
    n = len(fs) - len(gone)
    line = f'{n} file{"" if n == 1 else "s"}'
    return [line + (f', {len(gone)} missing: {", ".join(gone)}' if gone else '')]


def brief(folder, conn: Optional[sqlite3.Connection] = None) -> str:
    """What a run in `folder` is told of the project's knowledge cards ('' when none)."""
    return ''


def call(comp: Component, c: Call) -> Result:
    return Result(error=f'{comp.name} is a knowledge card: it is not called with eki use')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False
