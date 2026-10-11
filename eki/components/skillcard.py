"""The `skills` kind: a project's own skills, kept in the project.

The card folder (`components/skills/` from the template) holds one sub-folder
per skill, `<name>/SKILL.md` plus its files, versioned with the project. Runs
in the project or its worktrees get them on top of eki's store: Claude Code an
extra `--plugin-dir` (a wrapper under `EKI_HOME/project-plugins`), Codex a
per-run folder of links (`EKI_HOME/run-skills/<run>`). Nothing is written
into ~/.claude, ~/.codex or ~/.agents; runs elsewhere and bare agents get none.
A broken skill (no SKILL.md, no front matter or description) is reported and
skipped. `eki use <card>` lists what it holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from .. import paths, skills
from . import Call, Component, Result, find, templates_dir

ABOUT = "know-how this project's agents pick up when a task calls for it"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Skills"
EXPLAIN = ("Short how-to guides the project's agents pick up when a task calls for them, like how to add a level "
           "or how to write a scene. They live in the project, so they change with it and every agent gets the "
           "same ones. Add one when you notice agents keep needing the same advice.")


def validate(spec: dict, folder: Path) -> List[str]:
    return []


def held(card: Path) -> Tuple[List[dict], List[Tuple[str, str]]]:
    """The good skills in `card` by name, and (sub-folder, reason) for each broken one."""
    good: List[dict] = []
    broken: List[Tuple[str, str]] = []
    try:
        subs = sorted(p for p in Path(card).iterdir() if p.is_dir() and not p.name.startswith('.'))
    except OSError:
        return [], []
    for d in subs:
        md = d / 'SKILL.md'
        if not md.is_file():
            broken.append((str(d), 'no SKILL.md'))
            continue
        try:
            text = md.read_text(errors='replace')
        except OSError as e:
            broken.append((str(d), f'SKILL.md unreadable: {e}'))
            continue
        if not text.startswith('---'):
            broken.append((str(d), 'SKILL.md has no front matter'))
            continue
        desc = skills._front(text).get('description', '')
        if not desc:
            broken.append((str(d), 'SKILL.md front matter has no description'))
            continue
        good.append({'name': d.name, 'description': desc, 'path': str(d)})
    return good, broken


def _ensure(card: Path) -> None:
    card.mkdir(parents=True, exist_ok=True)
    spec = card / 'component.json'
    if not spec.exists():
        shutil.copyfile(templates_dir() / 'skills' / 'component.json', spec)


def add(card: Path, source: str, replace: bool = False) -> str:
    """Copy a skill from a folder or a git URL into the card; its name."""
    source = (source or '').strip()
    if not source:
        raise ValueError('a skill folder or git URL is needed')
    card = Path(card)
    _ensure(card)
    if skills.is_git(source):
        return skills._from_git(source, card, replace)
    return skills._copy(Path(source).expanduser().resolve(), '', card, replace)


def remove(card: Path, name: str) -> bool:
    """Delete the skill folder `card/<name>`; False when there's none."""
    if not name or '/' in name or name in ('.', '..') or name.startswith('.'):
        return False
    dest = Path(card) / name
    if dest.is_symlink() or not dest.is_dir():
        return False
    shutil.rmtree(dest)
    return True


def cards_for(folder, conn: Optional[sqlite3.Connection] = None) -> List[Path]:
    """The project skills cards a run in `folder` (a project or a worktree of it) gets."""
    if not folder:
        return []
    found, _ = find(folder, conn)
    return [c.folder for c in found.values() if c.kind == 'skills' and c.scope == 'project']


def _good(cards: List[Path]) -> List[dict]:
    """Every good skill across the cards; a name already taken by an earlier card is skipped."""
    out, seen = [], set()
    for card in cards:
        for s in held(card)[0]:
            if s['name'] not in seen:
                seen.add(s['name'])
                out.append(s)
    return out


def _link_into(target: Path, got: List[dict]) -> None:
    """`target` holds exactly one link per skill in `got`; stale links removed."""
    target.mkdir(parents=True, exist_ok=True)
    want = {s['name']: s['path'] for s in got}
    for p in target.iterdir():
        if p.is_symlink() and os.readlink(p) != want.get(p.name):
            p.unlink()
    for name, src in want.items():
        link = target / name
        if not link.is_symlink() and not link.exists():
            link.symlink_to(src, target_is_directory=True)


def _key(cards: List[Path]) -> str:
    joined = '\n'.join(sorted(str(Path(c).resolve()) for c in cards))
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def claude_plugin(cards: List[Path]) -> Optional[str]:
    """A plugin folder holding the cards' skills, for an extra `claude --plugin-dir`."""
    got = _good(cards)
    if not got:
        return None
    key = _key(cards)
    root = paths.home() / 'project-plugins' / key
    (root / '.claude-plugin').mkdir(parents=True, exist_ok=True)
    (root / '.claude-plugin' / 'plugin.json').write_text(json.dumps(
        {'name': f'eki-project-{key}', 'version': '1.0.0',
         'description': "Skills from the project's skills card"}, indent=2))
    _link_into(root / 'skills', got)
    return str(root)


def codex_dir(cards: List[Path], run_id: str) -> Optional[str]:
    """A folder of links to the cards' skills for one Codex run."""
    got = _good(cards)
    if not got or not run_id or '/' in run_id or run_id.startswith('.'):
        return None
    target = paths.home() / 'run-skills' / run_id
    _link_into(target, got)
    return str(target)


def _listing(card: Path) -> str:
    good, broken = held(card)
    lines = [f'{s["name"]} — {s["description"]}' for s in good]
    lines += [f'broken: {where}: {why}' for where, why in broken]
    return '\n'.join(lines) or '(no skills)'


def call(comp: Component, c: Call) -> Result:
    if c.args and c.args != ['ls']:
        return Result(error=f'{comp.name}: use: ls (add and remove with `eki component skills`)')
    return Result(text=_listing(comp.folder), paths=[str(comp.folder)])


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    good, broken = held(comp.folder)
    out = [f'skill {s["name"]} — {s["description"]}' for s in good]
    out += [f'broken skill {Path(where).name}: {why}' for where, why in broken]
    return out
