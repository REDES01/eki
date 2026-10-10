"""Components: concrete pieces any agent can call with `eki use <name>`.

A component is a folder `components/<name>/component.json` in a project
folder, or `~/.eki/components/<name>/` in the machine-wide pool. A project's
own component wins over a pool one with the same name. A broken one is
reported and skipped; finding never raises.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .. import paths


@dataclass
class Component:
    name: str
    kind: str
    folder: Path
    spec: dict
    scope: str


@dataclass
class Call:
    args: List[str]
    caller_dir: str
    caller_run: Optional[str]
    run_id: str
    emit: Callable[[str, dict], None]
    extra: dict


@dataclass
class Result:
    text: str = ''
    paths: List[str] = field(default_factory=list)
    error: str = ''


KINDS = {'service': 'eki.components.service', 'assets': 'eki.components.assets', 'agent': 'eki.components.agent'}
ACCESS = ('queue', 'exclusive', 'shared')
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
RESERVED = {'sessions'}


def pool_dir() -> Path:
    return paths.home() / 'components'


def sessions_dir() -> Path:
    return pool_dir() / 'sessions'


def templates_dir() -> Path:
    return Path(__file__).parent / 'templates'


def kind_module(kind: str):
    if kind not in KINDS:
        raise KeyError(f'unknown component kind {kind!r} (kinds: {", ".join(KINDS)})')
    return importlib.import_module(KINDS[kind])


def project_root(folder, conn: Optional[sqlite3.Connection] = None) -> Optional[Path]:
    """The folder whose components/ a run in `folder` sees: the main checkout of a worktree,
    and for a GitHub clone eki keeps, the person's own folder recorded on its project row."""
    if not folder:
        return None
    try:
        root = Path(folder).expanduser().resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    try:
        out = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                             capture_output=True, text=True, timeout=10)
        common = out.stdout.strip() if out.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        common = ''
    if common and Path(common).name == '.git':
        root = Path(common).parent.resolve()
    if conn is not None:
        try:
            row = conn.execute('SELECT folder FROM projects WHERE path=? AND COALESCE(folder, "")!=""',
                               (str(root),)).fetchone()
        except sqlite3.Error:
            row = None
        if row and Path(row['folder']).expanduser().is_dir():
            root = Path(row['folder']).expanduser().resolve()
    return root


def _check_common(spec: dict) -> List[str]:
    errs = []
    if spec.get('kind') not in KINDS:
        errs.append(f'kind must be one of {", ".join(KINDS)} (got {spec.get("kind")!r})')
    if not isinstance(spec.get('about'), str) or not spec.get('about').strip():
        errs.append('about is required: one line on what it does')
    if spec.get('access', 'shared') not in ACCESS:
        errs.append(f'access must be one of {", ".join(ACCESS)}')
    slots = spec.get('slots', 1)
    if not isinstance(slots, int) or isinstance(slots, bool) or slots < 1:
        errs.append('slots must be a whole number ≥ 1')
    if 'memory_gb' in spec:
        m = spec['memory_gb']
        if not isinstance(m, (int, float)) or isinstance(m, bool) or m <= 0:
            errs.append('memory_gb must be a number > 0')
    if 'conflicts' in spec:
        c = spec['conflicts']
        if not isinstance(c, list) or not all(isinstance(n, str) and NAME.match(n) for n in c):
            errs.append('conflicts must be a list of component names')
    if 'idle_stop' in spec:
        i = spec['idle_stop']
        if not isinstance(i, (int, float)) or isinstance(i, bool) or i < 0:
            errs.append('idle_stop must be minutes ≥ 0')
    return errs


def load(folder: Path, scope: str) -> Component:
    """The component in `folder`; raises ValueError with the reason when it's broken."""
    name = folder.name
    if not NAME.match(name) or name in RESERVED:
        raise ValueError(f'bad name {name!r}: lowercase letters, digits, - and _, not "sessions"')
    f = folder / 'component.json'
    try:
        spec = json.loads(f.read_text())
    except FileNotFoundError:
        raise ValueError('no component.json')
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f'component.json unreadable: {e}')
    if not isinstance(spec, dict):
        raise ValueError('component.json must hold an object')
    errs = _check_common(spec)
    if not errs:
        try:
            errs = list(kind_module(spec['kind']).validate(spec, folder) or [])
        except Exception as e:  # a kind's validate must never break finding
            errs = [f'validate failed: {type(e).__name__}: {e}']
    if errs:
        raise ValueError('; '.join(errs))
    return Component(name=name, kind=spec['kind'], folder=folder, spec=spec, scope=scope)


def _scan(base: Path, scope: str, found: Dict[str, Component], errors: List[Tuple[str, str]]) -> None:
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    except OSError as e:
        errors.append((str(base), f'unreadable: {e}'))
        return
    for d in entries:
        if scope == 'pool' and d.name in RESERVED:
            continue
        if d.name.startswith('.'):
            continue
        try:
            found[d.name] = load(d, scope)
        except Exception as e:
            errors.append((str(d), str(e)))


def find(folder, conn: Optional[sqlite3.Connection] = None
         ) -> Tuple[Dict[str, Component], List[Tuple[str, str]]]:
    """Components reachable from `folder` by name, and (folder, reason) for each broken one."""
    found: Dict[str, Component] = {}
    errors: List[Tuple[str, str]] = []
    try:
        _scan(pool_dir(), 'pool', found, errors)
        root = project_root(folder, conn)
        if root is not None:
            _scan(root / 'components', 'project', found, errors)
    except Exception as e:
        errors.append((str(folder), f'finding components failed: {e}'))
    return dict(sorted(found.items())), errors


def get(name: str, folder, conn: Optional[sqlite3.Connection] = None) -> Component:
    found, errors = find(folder, conn)
    if name in found:
        return found[name]
    for where, why in errors:
        if Path(where).name == name:
            raise KeyError(f'component {name} is broken ({where}): {why}')
    raise KeyError(f'no component {name} reachable from {folder} (reachable: {", ".join(found) or "none"})')


def _notes(c: Component) -> List[str]:
    try:
        return list(kind_module(c.kind).notes(c) or [])
    except Exception as e:
        return [f'notes failed: {e}']


def listing(folder, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    found, errors = find(folder, conn)
    out = [{'name': c.name, 'kind': c.kind, 'about': c.spec.get('about', ''), 'scope': c.scope,
            'folder': str(c.folder), 'error': '', 'notes': _notes(c)} for c in found.values()]
    pool = str(pool_dir())
    for where, why in errors:
        out.append({'name': Path(where).name, 'kind': '', 'about': '',
                    'scope': 'pool' if where.startswith(pool) else 'project',
                    'folder': where, 'error': why, 'notes': []})
    return out


def brief(folder, conn: Optional[sqlite3.Connection] = None) -> str:
    found, _ = find(folder, conn)
    if not found:
        return ''
    each = '; '.join(f'{c.name} — {c.spec.get("about", "")}' for c in found.values())
    return f'Components you can call with `eki use <name> [args…]` (`eki use` lists them): {each}'
