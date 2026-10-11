"""The `knowledge` kind: documents the agents of the project read — decisions, design rules,
contracts, notes. Read or opened, never called with `eki use`.

A card holds a list of documents (see knowledgedocs: shape, validation, files). Each document's
`"read"` is "always" (every run reads it first, planned or not) or "when-relevant" (the planner
picks it per item; other runs look there when its `about` line fits). This module is the brief.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from . import Call, Component, Result
from .. import components
from .knowledgedocs import (CHOICE_HELP, CHOICES, READ, author, bad_pattern, doc_files, docs, expand, files,
                            hits, inside, notes, resolve, root_of, validate)

__all__ = ['ABOUT', 'CHOICES', 'CHOICE_HELP', 'EXPLAIN', 'READ', 'TITLE', 'author', 'brief', 'call', 'doc_files',
           'docs', 'files', 'is_up', 'names', 'notes', 'resolve', 'root_of', 'stop', 'validate']

ABOUT = "documents the project's agents read: always, or when a task needs them"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Documents"
EXPLAIN = ("Documents the project's agents read before they work, such as decisions you made, design rules or "
           "the story so far. Some are read on every task; others only when a task needs them. Keep them here so "
           "agents follow what was already decided instead of guessing.")


def _brief_paths(root: Path, pattern: str) -> List[str]:
    """What the brief names for one pattern, relative to `root`: a folder as itself with a trailing
    /, a glob as its existing matches, a file as itself; nothing when it is missing or empty."""
    if bad_pattern(pattern) or not expand(root, pattern):
        return []
    out = []
    for h in hits(root, pattern):
        r = inside(root, Path(h))
        if r is None or not (r.is_file() or (r.is_dir() and expand(root, str(r.relative_to(root))))):
            continue
        rel = str(r.relative_to(root))
        name = rel + '/' if r.is_dir() else rel
        if name not in out:
            out.append(name)
    return out


def _found(doc: dict, root: Path) -> List[str]:
    found: List[str] = []
    for pattern in doc.get('files') or []:
        found += [p for p in _brief_paths(root, pattern) if p not in found]
    return found


def _line(doc: dict, root: Path, first: Optional[bool] = None) -> str:
    found = _found(doc, root)
    if not found:
        return ''
    first = doc.get('read') == 'always' if first is None else first
    how = 'read first' if first else 'look here when relevant'
    return f'- {how}: {", ".join(found)} — {_label(doc)}'


def _label(doc: dict) -> str:
    about = doc.get('about') or ''
    return f'{doc["name"]}: {about}' if doc.get('_named') else about


def names(raw) -> Optional[List[str]]:
    """The document or card names in an items.context value (JSON [{name, why}]); None for NULL or unreadable."""
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
        if not isinstance(f, str) or bad_pattern(f):
            continue
        r = inside(top, top / f)
        if r is not None and r.is_file():
            about = c.spec.get('about') or "the project's instructions"
            line = f'- read first: {r.relative_to(top)} — {about}'
            if line not in out:
                out.append(line)
    return out


def _pairs(cards: List[Component]) -> List[Tuple[Component, dict]]:
    """Every (card, document), always-documents first, then by card name, then card order. A
    card in the docs shape names each document in its line; an old one-document card doesn't."""
    out = []
    for c in cards:
        named = 'docs' in (c.spec or {})
        out += [(c, {**d, '_named': named}) for d in docs(c)]
    return sorted(out, key=lambda cd: (cd[1].get('read') != 'always', cd[0].name))


def _chosen(pairs: List[Tuple[Component, dict]], found: dict, top: Path, context: List[str]) -> List[str]:
    """The lines for a planned item: always-documents, the documents its context picks and the
    instructions read first; the rest named once, by document name, as available."""
    picked = {(c.name, d['name']) for c, d in resolve([c for c, _ in pairs], context)}
    first = [(c, d) for c, d in pairs if d.get('read') == 'always' or (c.name, d['name']) in picked]
    lines = [ln for ln in (_line(d, top, True) for _, d in first) if ln] + _instructions(found, top)
    rest = sorted({d['name'] for c, d in pairs if (c, d) not in first and _found(d, top)})
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
    """What a run in `folder` is told of the project's knowledge documents ('' when none). The
    cards come from the project; their files are named relative to the run's own checkout and
    kept only when they exist there. `context` (document or card names a planner picked for the
    item) narrows 'read first' to always-documents, those picked and the instructions; None is
    the full brief."""
    try:
        root = components.project_root(folder, conn)
        if root is None or not components.is_project(root, conn):
            return ''
        found, _ = components.own(root)
        cards = sorted((c for c in found.values() if c.kind == 'knowledge'), key=lambda c: c.name)
        pairs = _pairs(cards)
        if not pairs:
            return ''
        top = _own_top(folder, root)
        if context is None:
            lines = [ln for ln in (_line(d, top) for _, d in pairs) if ln]
        else:
            lines = _chosen(pairs, found, top, list(context))
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
