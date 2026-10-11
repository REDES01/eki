"""Components: concrete pieces any agent can call with `eki use <name>`.

A component is a folder `components/<name>/component.json` in a project
folder, or `~/.eki/components/<name>/` in the machine-wide pool. A project
assumes nothing: from a project folder only its own components/ is reachable,
a pool component through a card `components/<name>/component.json` holding
just `{"pool": "<name>"}`. With no project, the whole pool is. A broken one is
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
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import paths


@dataclass
class Component:
    name: str
    kind: str
    folder: Path
    spec: dict
    scope: str
    card: Optional[Path] = None   # the card folder, for a pool component reached through a card


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


KINDS = {'service': 'eki.components.service', 'assets': 'eki.components.assets', 'agent': 'eki.components.agent',
         'skills': 'eki.components.skillcard', 'instructions': 'eki.components.instructions',
         'serve': 'eki.components.serve', 'knowledge': 'eki.components.knowledge',
         'mcp': 'eki.components.mcpserver'}
#: kinds that are read or attached, never called with `eki use`
PASSIVE = ('knowledge', 'mcp')
ACCESS = ('queue', 'exclusive', 'shared')
#: settings every kind has whose values come from a fixed set; a kind adds its own
#: in its module's CHOICES (and CHOICE_DEFAULTS for the value used when the key is absent)
CHOICES = {'access': list(ACCESS)}
CHOICE_DEFAULTS = {'access': 'shared'}
#: one line per value of a fixed choice, shown on hover; a kind adds its own in CHOICE_HELP
CHOICE_HELP = {'access': {'queue': 'Calls wait in line and are served in order, a few at a time (slots).',
                          'exclusive': 'One run holds it from first use until that run ends; others wait.',
                          'shared': 'Any number of runs can use it at once.'}}
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


def _merged(kind: str, attr: str, common: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: list(v) if isinstance(v, list) else v for k, v in common.items()}
    try:
        own_ = getattr(kind_module(kind), attr, None) if kind in KINDS else None
    except Exception:  # a kind that won't import still gets the common ones
        own_ = None
    if isinstance(own_, dict):
        out.update({k: list(v) if isinstance(v, (list, tuple)) else v for k, v in own_.items()})
    return out


def choices(kind: str) -> Dict[str, list]:
    """Setting key -> its allowed values for `kind`: the common ones and the kind's own. Never raises."""
    return _merged(kind, 'CHOICES', CHOICES)


def choice_defaults(kind: str) -> Dict[str, Any]:
    """Setting key -> the value used when a spec of `kind` lacks the key. Never raises."""
    return _merged(kind, 'CHOICE_DEFAULTS', CHOICE_DEFAULTS)


def _json_key(v: Any) -> str:
    return ('true' if v else 'false') if isinstance(v, bool) else str(v)


def choice_help(kind: str) -> Dict[str, Dict[str, str]]:
    """Setting key -> value (as JSON writes it: true/false) -> one line on what it means. Never raises."""
    out: Dict[str, Dict[str, str]] = {}
    for key, texts in _merged(kind, 'CHOICE_HELP', CHOICE_HELP).items():
        if isinstance(texts, dict):
            out[key] = {_json_key(v): str(t) for v, t in texts.items()}
    return out


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
            row = conn.execute('SELECT folder FROM projects WHERE path=? AND COALESCE(folder, "")!="" '
                               'AND COALESCE(state, "on") NOT IN ("deleting", "deleted")',
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
    if spec.get('access', CHOICE_DEFAULTS['access']) not in CHOICES['access']:
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


def _ref(folder: Path) -> Optional[dict]:
    """The card's JSON when `folder` is a pool reference card (exactly {"pool": …}), else None."""
    try:
        spec = json.loads((folder / 'component.json').read_text())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return spec if isinstance(spec, dict) and list(spec) == ['pool'] else None


def load_card(folder: Path, ref: dict) -> Component:
    """The pool component a reference card names, with `card` set; ValueError when it's broken."""
    name = ref.get('pool')
    if not isinstance(name, str) or not NAME.match(name) or name in RESERVED:
        raise ValueError(f'pool card names {name!r}: not a component name')
    if name != folder.name:
        raise ValueError("a pool card's folder must be named like its pool component")
    target = pool_dir() / name
    if not (target / 'component.json').is_file():
        raise ValueError(f'the pool has no component {name}')
    try:
        comp = load(target, 'pool')
    except ValueError as e:
        raise ValueError(f'pool component {name} is broken: {e}')
    comp.card = folder
    return comp


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
            ref = _ref(d) if scope == 'project' else None
            found[d.name] = load_card(d, ref) if ref is not None else load(d, scope)
        except Exception as e:
            errors.append((str(d), str(e)))


def is_project(root: Path, conn: Optional[sqlite3.Connection] = None) -> bool:
    """A project folder: it has components/, or it is a listed project's folder (eki's own too).
    The folders come from projectchats' cheap map, not its full listing: finding runs often."""
    if (root / 'components').is_dir():
        return True
    from .. import db, projectchats
    try:
        c = conn if conn is not None else db.connect()
        try:
            return projectchats.norm(root) in projectchats._folders(c)
        finally:
            if conn is None:
                c.close()
    except Exception:  # an unreadable db: not known
        return False


def find(folder, conn: Optional[sqlite3.Connection] = None
         ) -> Tuple[Dict[str, Component], List[Tuple[str, str]]]:
    """Components reachable from `folder` by name, and (folder, reason) for each broken one."""
    found: Dict[str, Component] = {}
    errors: List[Tuple[str, str]] = []
    try:
        root = project_root(folder, conn)
        if root is not None and is_project(root, conn):
            _scan(root / 'components', 'project', found, errors)
        else:
            _scan(pool_dir(), 'pool', found, errors)
    except Exception as e:
        errors.append((str(folder), f'finding components failed: {e}'))
    return dict(sorted(found.items())), errors


def own(root: Path) -> Tuple[Dict[str, Component], List[Tuple[str, str]]]:
    """A project's cards: only what root/components holds (own components and pool cards)."""
    found: Dict[str, Component] = {}
    errors: List[Tuple[str, str]] = []
    try:
        _scan(Path(root) / 'components', 'project', found, errors)
    except Exception as e:
        errors.append((str(root), f'finding components failed: {e}'))
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
    return entries(*find(folder, conn))


def entries(found: Dict[str, Component], errors: List[Tuple[str, str]]) -> List[dict]:
    """One dict per component found, then one per broken one (with its error)."""
    out = [{'name': c.name, 'kind': c.kind, 'about': c.spec.get('about', ''), 'scope': c.scope,
            'folder': str(c.folder), 'error': '', 'notes': _notes(c),
            'card': str(c.card or (c.folder if c.scope == 'project' else '')),
            'ref': c.name if c.card else ''} for c in found.values()]
    pool = str(pool_dir())
    for where, why in errors:
        in_pool = where == pool or where.startswith(pool + '/')
        out.append({'name': Path(where).name, 'kind': '', 'about': '',
                    'scope': 'pool' if in_pool else 'project',
                    'folder': where, 'error': why, 'notes': [], 'card': '' if in_pool else where, 'ref': ''})
    return out


def brief(folder, conn: Optional[sqlite3.Connection] = None) -> str:
    found, _ = find(folder, conn)
    callable_ = [c for c in found.values() if c.kind not in PASSIVE]
    if not callable_:
        return ''
    each = '; '.join(f'{c.name} — {c.spec.get("about", "")}' for c in callable_)
    return f'Components you can call with `eki use <name> [args…]` (`eki use` lists them): {each}'
