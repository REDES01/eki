"""Merge a project's cards into one: `eki component merge <project> knowledge|service <into> …`.

knowledge: the named knowledge cards (all of the project's when none are named) become the
documents of one card <into> ({"kind": "knowledge", "about", "docs": […]}); an old one-document
card keeps its name, files, about and read; an existing <into> keeps its documents.
service: a service card plus an mcp card become one service card <into> with "mcp" = the mcp
card's "server", the stricter access (exclusive > queue > shared) and the union of conflicts.

Both rewrite every other card's conflicts naming a merged card to <into> (deduped, never itself),
move the old folders to the Trash (never <into>'s, never a real delete) and write component.json
files only: nothing is committed. `plan()` is the dry run; `apply()` does it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .. import components
from .cards import to_trash
from .knowledgedocs import docs as _card_docs

STRICT = {'shared': 0, 'queue': 1, 'exclusive': 2}


def _spec(base: Path, name: str) -> dict:
    f = Path(base) / name / 'component.json'
    try:
        spec = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f'no readable card {name} in {base}: {e}')
    if not isinstance(spec, dict):
        raise ValueError(f'{f} is not a JSON object')
    return spec


def _all(base: Path) -> Dict[str, dict]:
    """Every card folder of `base` with a readable component.json, by name."""
    out = {}
    for d in sorted(Path(base).iterdir()) if Path(base).is_dir() else []:
        if d.is_dir() and not d.name.startswith('.') and (d / 'component.json').is_file():
            try:
                out[d.name] = _spec(base, d.name)
            except ValueError:
                continue
    return out


def _docs_of(name: str, spec: dict) -> List[dict]:
    comp = components.Component(name=name, kind='knowledge', scope='project', folder=Path(name), spec=spec)
    return [{'name': d['name'], 'files': d['files'], 'about': d['about'], 'read': d.get('read') or 'when-relevant'}
            for d in _card_docs(comp)]


def _free(name: str, taken: set) -> str:
    n, out = 2, name
    while out in taken:
        out, n = f'{name}-{n}', n + 1
    return out


def _check(name: str, kind: str, spec: dict, base: Path) -> None:
    errs = components.kind_module(kind).validate(spec, Path(base) / name) or []
    if errs:
        raise ValueError(f'the merged card {name} would be broken: {"; ".join(errs)}')


def knowledge(base: Path, into: str, names: Optional[List[str]] = None, about: Optional[str] = None) -> dict:
    """The plan folding knowledge cards into one card <into>."""
    cards = _all(base)
    names = list(names or [n for n, s in cards.items() if s.get('kind') == 'knowledge' and n != into])
    for n in names:
        if n not in cards:
            raise ValueError(f'no card {n} in {base}')
        if cards[n].get('kind') != 'knowledge':
            raise ValueError(f'{n} is a {cards[n].get("kind")} card, not knowledge')
    old = cards.get(into)
    if old is not None and old.get('kind') != 'knowledge':
        raise ValueError(f'{into} is a {old.get("kind")} card, not knowledge')
    ds = _docs_of(into, old) if old is not None else []
    taken = {d['name'] for d in ds}
    for n in names:
        if n == into:
            continue
        for d in _docs_of(n, cards[n]):
            d['name'] = _free(d['name'], taken)
            taken.add(d['name'])
            ds.append(d)
    if not ds:
        raise ValueError('no knowledge documents to merge')
    spec = {'kind': 'knowledge',
            'about': about or (old or {}).get('about') or "the project's shared documents"}
    if old:
        spec.update({k: v for k, v in old.items() if k not in ('kind', 'about', 'files', 'read', 'docs')})
    spec['docs'] = ds
    _check(into, 'knowledge', spec, base)
    return _finish(base, into, spec, [n for n in names if n != into], cards)


def service(base: Path, into: str, svc: str, mcp: str, about: Optional[str] = None) -> dict:
    """The plan writing one service card <into> from the service card <svc> and the mcp card <mcp>."""
    cards = _all(base)
    for n, kind in ((svc, 'service'), (mcp, 'mcp')):
        if n not in cards:
            raise ValueError(f'no card {n} in {base}')
        if cards[n].get('kind') != kind:
            raise ValueError(f'{n} is a {cards[n].get("kind")} card, not {kind}')
    if into in cards and into not in (svc, mcp):
        raise ValueError(f'{into} is already another card: pick a free name, or {svc} or {mcp}')
    s, m = cards[svc], cards[mcp]
    spec = {k: v for k, v in s.items() if k not in ('access', 'conflicts', 'mcp')}
    if about:
        spec['about'] = about
    spec['mcp'] = m.get('server')
    access = max((x.get('access') for x in (s, m) if x.get('access') in STRICT), key=STRICT.get, default=None)
    if access:
        spec['access'] = access
    merged = {svc, mcp, into}
    conflicts = []
    for c in list(s.get('conflicts') or []) + list(m.get('conflicts') or []):
        if c not in merged and c not in conflicts:
            conflicts.append(c)
    if conflicts:
        spec['conflicts'] = conflicts
    _check(into, 'service', spec, base)
    return _finish(base, into, spec, [n for n in (svc, mcp) if n != into], cards)


def _finish(base: Path, into: str, spec: dict, gone: List[str], cards: Dict[str, dict]) -> dict:
    """The plan: <into>'s spec, the other cards whose conflicts change, the folders to the Trash."""
    merged = set(gone) | {into}
    others = {}
    for n, s in cards.items():
        if n in merged or not isinstance(s.get('conflicts'), list):
            continue
        before = s['conflicts']
        after: List[str] = []
        for c in before:
            c = into if c in merged else c
            if c != n and c not in after:
                after.append(c)
        if after != before:
            new = dict(s)
            if after:
                new['conflicts'] = after
            else:
                new.pop('conflicts')
            others[n] = new
    return {'base': str(base), 'into': into, 'spec': spec, 'others': others, 'trash': gone,
            'existed': into in cards}


def describe(plan: dict) -> List[str]:
    into, spec = plan['into'], plan['spec']
    what = (f'{len(spec["docs"])} documents: ' + ', '.join(f'{d["name"]} ({d["read"]})' for d in spec['docs'])
            if spec['kind'] == 'knowledge' else
            f'service, access {spec.get("access", "shared")}, mcp {spec["mcp"].get("command") or spec["mcp"].get("url")}'
            f', conflicts {", ".join(spec.get("conflicts") or []) or "none"}')
    lines = [f'{"rewrite" if plan["existed"] else "write"} {into}/component.json: {what}']
    for n, s in plan['others'].items():
        lines.append(f'{n}: conflicts now {", ".join(s.get("conflicts") or []) or "none"}')
    for n in plan['trash']:
        lines.append(f'{n}/ to the Trash')
    return lines


def _write(f: Path, spec: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + '.tmp')
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n')
    tmp.replace(f)


def apply(plan: dict) -> List[str]:
    """Write the plan; what changed, one line each."""
    base = Path(plan['base'])
    _write(base / plan['into'] / 'component.json', plan['spec'])
    for n, s in plan['others'].items():
        _write(base / n / 'component.json', s)
    lines = describe(plan)
    out = lines[:len(lines) - len(plan['trash'])]
    for n in plan['trash']:
        out.append(f'{n}/ moved to the Trash: {to_trash(base / n)}')
    return out
