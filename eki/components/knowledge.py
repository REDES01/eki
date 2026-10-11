"""The `knowledge` kind: files the agents of the project read — decisions, design rules,
contracts, notes. Read or opened, never called with `eki use`.

`"files"` are paths relative to the project root (globs and folders allowed, no absolute
path, no ".."); `"read"` is "always" (every run reads it first, planned or not) or
"when-relevant" (the planner picks it per item; other runs look there when the `about` line
fits). A pattern that matches nothing is shown missing and left out of the brief.
"""
from __future__ import annotations

import glob
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Optional

from . import Call, Component, Result
from .. import components

ABOUT = "documents the project's agents read: always, or when a task needs them"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Documents"
EXPLAIN = ("Documents the project's agents read before they work, such as decisions you made, design rules or "
           "the story so far. Some are read on every task; others only when a task needs them. Keep them here so "
           "agents follow what was already decided instead of guessing.")
#: settings whose values come from a fixed set (read has no default: it is required)
CHOICES = {'read': ['always', 'when-relevant']}
READ = tuple(CHOICES['read'])
CHOICE_HELP = {'read': {'always': 'Every run in this project reads it first, planned or not: '
                                  'keep it for the few cards that apply to all work.',
                        'when-relevant': 'The planner picks it for the items that need it; other runs are '
                                         'told it exists and open it when their work touches it.'}}


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
    if spec.get('read') not in CHOICES['read']:
        errs.append(f'read must be one of {", ".join(CHOICES["read"])} (got {spec.get("read")!r})')
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


def _found(comp: Component, root: Path) -> List[str]:
    found: List[str] = []
    for pattern in comp.spec.get('files') or []:
        found += [p for p in _brief_paths(root, pattern) if p not in found]
    return found


def _line(comp: Component, root: Path, first: Optional[bool] = None) -> str:
    found = _found(comp, root)
    if not found:
        return ''
    first = comp.spec.get('read') == 'always' if first is None else first
    how = 'read first' if first else 'look here when relevant'
    return f'- {how}: {", ".join(found)} — {comp.spec.get("about", "")}'


def names(raw) -> Optional[List[str]]:
    """The card names in an items.context value (JSON [{name, why}]); None for NULL or unreadable."""
    if raw is None:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    out = [str(e.get('name') if isinstance(e, dict) else e).strip() for e in data
           if (e.get('name') if isinstance(e, dict) else e)]
    return [n for n in out if n]


def _instructions(found: dict, top: Path) -> List[str]:
    """'read first' lines for the project's instructions cards whose file exists in the checkout."""
    out = []
    for c in sorted((c for c in found.values() if c.kind == 'instructions'), key=lambda c: c.name):
        f = c.spec.get('file') or 'AGENTS.md'
        if not isinstance(f, str) or _bad_pattern(f):
            continue
        r = _inside(top, top / f)
        if r is not None and r.is_file():
            about = c.spec.get('about') or "the project's instructions"
            line = f'- read first: {r.relative_to(top)} — {about}'
            if line not in out:
                out.append(line)
    return out


def _chosen(cards: List[Component], found: dict, top: Path, context: List[str]) -> List[str]:
    """The lines for a planned item: always-cards, its chosen cards and the instructions read
    first; the rest named once as available."""
    picked = set(context)
    first = [c for c in cards if c.spec.get('read') == 'always' or c.name in picked]
    lines = [ln for ln in (_line(c, top, True) for c in first) if ln] + _instructions(found, top)
    rest = [c.name for c in cards if c not in first and _found(c, top)]
    if rest:
        lines.append(f'Also available if you need them: {", ".join(rest)} (see components/).')
    return lines


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


def brief(folder, conn: Optional[sqlite3.Connection] = None, *,
          context: Optional[List[str]] = None) -> str:
    """What a run in `folder` is told of the project's knowledge cards ('' when none). The cards
    come from the project; their files are named relative to the run's own checkout and kept
    only when they exist there. `context` (card names a planner picked for the item) narrows
    'read first' to always-cards, those names and the instructions; None is the full brief."""
    try:
        root = components.project_root(folder, conn)
        if root is None or not components.is_project(root, conn):
            return ''
        found, _ = components.own(root)
        cards = sorted((c for c in found.values() if c.kind == 'knowledge'),
                       key=lambda c: (c.spec.get('read') != 'always', c.name))
        if not cards:
            return ''
        top = _own_top(folder, root)
        if context is None:
            lines = [ln for ln in (_line(c, top) for c in cards) if ln]
        else:
            lines = _chosen(cards, found, top, list(context))
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
