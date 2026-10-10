"""The `instructions` kind: the project's own AGENTS.md (`"file"`, relative to the root) as a card.

eki keeps no copy: it reads the file, writes it when edited on the card, and writes a starter plus
a CLAUDE.md holding only `@AGENTS.md` only when missing. Never overwrites, never commits.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import List

from . import Call, Component, Result

CLAUDE = 'CLAUDE.md'
TICKED = re.compile(r'`([^`\n]+)`')
HEADING = re.compile(r'^#{1,6}\s+(.*?)\s*#*\s*$')


def validate(spec: dict, folder: Path) -> List[str]:
    f = spec.get('file')
    if not isinstance(f, str) or not f.strip() or Path(f).is_absolute() or '..' in Path(f).parts:
        return [f'file must be a path inside the project with no "..", like AGENTS.md (got {f!r})']
    return []


def root_of(comp: Component) -> Path:
    """The project root: the card's project for a project card (or a pool one reached through a card)."""
    if comp.card is not None:
        return Path(comp.card).parent.parent
    return comp.folder.parent.parent if comp.scope == 'project' else comp.folder


def safe_path(root: Path, rel: str) -> Path:
    """`rel` resolved inside root; ValueError on an absolute path, a '..' or an escape."""
    p = Path(rel if isinstance(rel, str) else '')
    if p.is_absolute() or '..' in p.parts:
        raise ValueError(f'{rel!r} must be a path inside the project, with no ".."')
    base = Path(root).resolve()
    target = (base / p).resolve()
    if target == base or base not in target.parents:
        raise ValueError(f'{rel!r} leads outside the project')
    return target


def read_first(root: Path, text: str) -> List[str]:
    """The backticked paths in `text` that are existing files inside root, first seen first."""
    skip = {Path(root).resolve() / n for n in (CLAUDE, 'AGENTS.md')}
    out: List[str] = []
    for m in TICKED.finditer(text or ''):
        rel = m.group(1).strip()
        try:
            p = safe_path(root, rel)
        except ValueError:
            continue
        if p.is_file() and p not in skip and rel not in out:
            out.append(rel)
    return out


def claude_state(root: Path, file: str = 'AGENTS.md') -> str:
    """'none' (no CLAUDE.md), 'linked' (it has the line @<file>) or 'unlinked'."""
    p = Path(root) / CLAUDE
    if not p.is_file():
        return 'none'
    lines = p.read_text(errors='replace').splitlines()
    return 'linked' if any(ln.strip() == '@' + file for ln in lines) else 'unlinked'


def save(root: Path, file: str, text: str) -> None:
    """Write root/file atomically (a temp file beside it, then os.replace)."""
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    (p := safe_path(root, file)).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f'.{p.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def link_claude(root: Path, file: str = 'AGENTS.md') -> bool:
    """Add the line @<file> to CLAUDE.md (made when missing); whether anything was written."""
    if (state := claude_state(root, file)) == 'linked':
        return False
    old = '' if state == 'none' else (Path(root) / CLAUDE).read_text(errors='replace')
    sep = '\n' if old and not old.endswith('\n') else ''
    save(root, CLAUDE, f'{old}{sep}@{file}\n')
    return True


def summary(root: Path, file: str = 'AGENTS.md') -> dict:
    try:
        p = safe_path(root, file)
        exists = p.is_file()
        text = p.read_text(errors='replace') if exists else ''
    except (OSError, ValueError):
        exists, text = False, ''
    heading = next((m.group(1) for m in map(HEADING.match, text.splitlines()) if m), '')
    return {'file': file, 'exists': exists, 'heading': heading, 'lines': len(text.splitlines()),
            'text': text, 'read_first': read_first(root, text), 'claude': claude_state(root, file)}


STARTER = ('# {name}\n\n## Read first\n\nThe files an agent should read before changing anything.\n\n'
           '## How to check your work\n\nThe command that tells whether a change is good.\n')


def ensure_files(root: Path, file: str = 'AGENTS.md') -> List[str]:
    """A starter <file> and a CLAUDE.md holding only @<file>, each only when missing."""
    said: List[str] = []
    if safe_path(root, file).exists():
        said.append(f'{file} exists: the card points at it')
    else:
        save(root, file, STARTER.format(name=Path(root).resolve().name))
        said.append(f'wrote {file} (a starter)')
    state = claude_state(root, file)
    if state == 'none' and not (Path(root) / CLAUDE).exists():
        save(root, CLAUDE, f'@{file}\n')
        said.append(f'wrote {CLAUDE} (@{file})')
    elif state == 'unlinked':
        said.append(f'{CLAUDE} exists without @{file}: Link CLAUDE.md on the card adds it')
    return said


def call(comp: Component, c: Call) -> Result:
    root, file = root_of(comp), comp.spec['file']
    s = summary(root, file)
    return Result(text=s['text']) if s['exists'] else Result(error=f'no {file} in {root}')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    file = comp.spec.get('file', 'AGENTS.md')
    s = summary(root_of(comp), file)
    if not s['exists']:
        return [f'{file} is missing']
    unlinked = [f"CLAUDE.md doesn't include @{file}"] if s['claude'] == 'unlinked' else []
    return [f'{s["heading"] or file}: {s["lines"]} line{"" if s["lines"] == 1 else "s"}'] + unlinked
