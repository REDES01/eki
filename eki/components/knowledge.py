"""The `knowledge` kind: files every agent of the project reads — decisions, design rules,
contracts, notes. Read or opened, never called with `eki use`.

`"files"` are paths relative to the project root (globs and folders allowed, no absolute
path, no ".."); `"read"` is "always" (read first) or "when-relevant" (look there when the
`about` line fits). A pattern that matches nothing is shown missing and left out of the brief.
"""
from __future__ import annotations

import glob
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Optional

from . import Call, Component, Result
from .. import components

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


def author(root: Path, p: Path) -> str:
    """Who last changed `p`, from git log in `root`; '' when it isn't in git. Never raises."""
    try:
        out = subprocess.run(['git', '-C', str(root), 'log', '-1', '--format=%an', '--', str(p)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


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
            'size': size, 'changed': changed, 'author': author(root, p), 'missing': False}


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


def _brief_paths(root: Path, pattern: str) -> List[str]:
    """What the brief names for one pattern, relative to `root`: a folder as itself with a trailing
    /, a glob as its existing matches, a file as itself; nothing when it is missing or empty."""
    if _bad_pattern(pattern) or not _expand(root, pattern):
        return []
    hits = sorted(glob.glob(str(root / pattern), recursive=True)) if glob.has_magic(pattern) else [str(root / pattern)]
    out = []
    for h in hits:
        r = _inside(root, Path(h))
        if r is None or not (r.is_file() or (r.is_dir() and _expand(root, str(r.relative_to(root))))):
            continue
        rel = str(r.relative_to(root))
        name = rel + '/' if r.is_dir() else rel
        if name not in out:
            out.append(name)
    return out


def _line(comp: Component, root: Path) -> str:
    found: List[str] = []
    for pattern in comp.spec.get('files') or []:
        found += [p for p in _brief_paths(root, pattern) if p not in found]
    if not found:
        return ''
    how = 'read first' if comp.spec.get('read') == 'always' else 'look here when relevant'
    return f'- {how}: {", ".join(found)} — {comp.spec.get("about", "")}'


def _own_top(folder, root: Path) -> Path:
    """The run's own checkout: the git top of `folder` (a worktree's, not the main one's), else
    `root`. Its copies are the ones the run reads and edits."""
    try:
        out = subprocess.run(['git', '-C', str(Path(folder).expanduser()), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, timeout=10)
        top = out.stdout.strip() if out.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError, ValueError):
        top = ''
    return Path(top).resolve() if top else root.resolve()


def brief(folder, conn: Optional[sqlite3.Connection] = None) -> str:
    """What a run in `folder` is told of the project's knowledge cards ('' when none). The cards
    come from the project; their files are named relative to the run's own checkout and kept
    only when they exist there."""
    try:
        root = components.project_root(folder, conn)
        if root is None or not components.is_project(root, conn):
            return ''
        found, _ = components.own(root)
        cards = sorted((c for c in found.values() if c.kind == 'knowledge'),
                       key=lambda c: (c.spec.get('read') != 'always', c.name))
        top = _own_top(folder, root)
        lines = [ln for ln in (_line(c, top) for c in cards) if ln]
    except Exception:  # a brief never breaks a run
        return ''
    if not lines:
        return ''
    return '\n'.join(['Shared knowledge of this project (knowledge cards in its components/; '
                      'paths are relative to this folder):'] + lines)


def call(comp: Component, c: Call) -> Result:
    return Result(error=f'{comp.name} is a knowledge card: it is not called with eki use')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False
