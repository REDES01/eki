"""A knowledge card's documents: their shape, validation, and the files each one names.

A card holds `"docs"`: a list of {"name", "files", "about", "read"}. The old one-document shape
(top-level `"files"` + `"read"`) still loads as one document named after the card, carrying
the card's about, files and read. `"files"` are paths relative to the project root (globs and
folders allowed, no absolute path, no ".."); a pattern that matches nothing is shown missing.
"""
from __future__ import annotations

import glob
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from . import Component

#: settings whose values come from a fixed set (read has no default: it is required)
CHOICES = {'read': ['always', 'when-relevant']}
READ = tuple(CHOICES['read'])
CHOICE_HELP = {'read': {'always': 'Every run in this project reads it first, planned or not: '
                                  'keep it for the few documents that apply to all work.',
                        'when-relevant': 'The planner picks it for the items that need it; other runs are '
                                         'told it exists and open it when their work touches it.'}}
_NAME = re.compile(r'[\w.-]+')


def bad_pattern(p) -> str:
    if not isinstance(p, str) or not p.strip():
        return 'files must be non-empty paths'
    if Path(p).is_absolute() or p.startswith('~'):
        return f'files {p!r} must be relative to the project'
    if '..' in Path(p).parts:
        return f'files {p!r} must stay inside the project, with no ".."'
    return ''


def _doc_errors(d: dict, where: str) -> List[str]:
    errs = []
    fs = d.get('files')
    if not isinstance(fs, list) or not fs:
        errs.append(f'{where}files must be a non-empty list of paths in the project (globs and folders allowed)')
    else:
        errs += [where + e for e in (bad_pattern(p) for p in fs) if e][:1]
    if d.get('read') not in CHOICES['read']:
        errs.append(f'{where}read must be one of {", ".join(CHOICES["read"])} (got {d.get("read")!r})')
    return errs


def validate(spec: dict, folder: Path) -> List[str]:
    if 'docs' not in spec:
        return _doc_errors(spec, '')
    if 'files' in spec:
        return ['use either "docs" (a list of documents) or top-level "files", not both']
    ds = spec.get('docs')
    if not isinstance(ds, list) or not ds:
        return ['docs must be a non-empty list of documents ({"name", "files", "about", "read"})']
    errs: List[str] = []
    seen = set()
    for i, d in enumerate(ds):
        if not isinstance(d, dict):
            errs.append(f'document {i + 1} must be an object with name, files, about and read')
            continue
        name = d.get('name')
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            errs.append(f'document {i + 1}: name must be letters, digits, _, . or - (got {name!r})')
            where = f'document {i + 1}: '
        else:
            where = f'document {name}: '
            if name in seen:
                errs.append(f'document {name}: the name is used twice in this card')
            seen.add(name)
        if not isinstance(d.get('about', ''), str):
            errs.append(f'{where}about must be text')
        errs += _doc_errors(d, where)
    return errs


def docs(comp: Component) -> List[dict]:
    """The card's documents in card order, each {"name", "files", "about", "read"}."""
    spec = comp.spec or {}
    if 'docs' not in spec:
        return [{'name': comp.name, 'files': list(spec.get('files') or []),
                 'about': spec.get('about') or '', 'read': spec.get('read')}]
    out = []
    for d in spec.get('docs') or []:
        if isinstance(d, dict) and isinstance(d.get('name'), str) and d['name']:
            fs = d.get('files')
            out.append({'name': d['name'], 'files': list(fs) if isinstance(fs, list) else [],
                        'about': d.get('about') if isinstance(d.get('about'), str) else '',
                        'read': d.get('read')})
    return out


def resolve(cards: List[Component], names: List[str]) -> List[Tuple[Component, dict]]:
    """The (card, doc) pairs `names` pick: a document name in any card, or a card name (all its
    documents, so old item contexts still resolve). Unknown names are ignored."""
    want = {str(n).strip() for n in names or [] if str(n).strip()}
    return [(c, d) for c in cards for d in docs(c) if d['name'] in want or c.name in want]


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


def inside(root: Path, p: Path) -> Optional[Path]:
    try:
        r = p.resolve()
    except OSError:
        return None
    return r if r == root or root in r.parents else None


def hits(root: Path, pattern: str) -> List[str]:
    return sorted(glob.glob(str(root / pattern), recursive=True)) if glob.has_magic(pattern) else [str(root / pattern)]


def expand(root: Path, pattern: str) -> List[Path]:
    if bad_pattern(pattern):
        return []
    out: List[Path] = []
    for h in hits(root, pattern):
        p = Path(h)
        if p.is_dir():
            out += sorted(f for f in p.rglob('*') if f.is_file()
                          and not any(part.startswith('.') for part in f.relative_to(p).parts))
        elif p.is_file():
            out.append(p)
    seen, kept = set(), []
    for p in out:
        r = inside(root, p)
        if r is not None and r not in seen:
            seen.add(r)
            kept.append(r)
    return kept


def doc_files(comp: Component, doc: dict) -> List[dict]:
    """One dict per file the document names; a pattern matching nothing gives one missing entry."""
    root = root_of(comp).resolve()
    out: List[dict] = []
    for pattern in doc.get('files') or []:
        found = expand(root, pattern)
        out += [_entry(pattern, root, p) for p in found] if found else [_entry(pattern, root, None)]
    return out


def files(comp: Component) -> List[dict]:
    """Every file entry of every document, each with "doc": the document's name."""
    return [{**e, 'doc': d['name']} for d in docs(comp) for e in doc_files(comp, d)]


def notes(comp: Component) -> List[str]:
    fs = files(comp)
    gone = [f['path'] for f in fs if f['missing']]
    n = len(fs) - len(gone)
    line = f'{n} file{"" if n == 1 else "s"}'
    if 'docs' in (comp.spec or {}):
        k = len(docs(comp))
        line = f'{k} document{"" if k == 1 else "s"}, {line}'
    return [line + (f', {len(gone)} missing: {", ".join(gone)}' if gone else '')]
