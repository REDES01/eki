"""The `mcp` kind: an MCP server only this project's runs get.

`"server"` is `{"command", "args", "env"}` (stdio) or `{"url"}`. Nothing goes into eki's
registry (~/.eki/mcp.json) or a program's own config: a project run gets its cards' servers
merged with the registry for that run only. Env values never leave whole: `public()` masks them.
A service card's `"mcp"` reaches runs the same way; for a run its command is wrapped in
`eki mcp-hold` so that using the server holds the card (access, conflicts) like a call.
A Playwright server in a run's config (a card's or the registry's) gets PLAYWRIGHT_BROWSERS_PATH
set to the browser shim in its own env — an MCP client may not pass its environment on — so its
browsers wait for the machine's browser slots; the registry file itself is never changed.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import browsershim, mcp, paths
from ..providers.codex import mcp_flags
from . import Call, Component, Result, own, project_root

ABOUT = "an outside program that gives only this project's agents extra abilities, like driving a browser"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Tool connection"
EXPLAIN = ("A connection to an outside program that gives this project's agents extra abilities, such as driving "
           "a browser or talking to a music program. Only agents working on this project get it. Agents use it "
           "on their own while they work; this card shows whether it starts and what it offers.")


def validate(spec: dict, folder: Path) -> List[str]:
    s = spec.get('server')
    if not isinstance(s, dict):
        return ['server must be {"command": …, "args": […], "env": {…}} or {"url": …}']
    if 'url' in s:
        if not isinstance(s['url'], str) or not s['url'].startswith(('http://', 'https://')):
            return ['server url must be an http(s) address']
        return []
    errs = []
    if not isinstance(s.get('command'), str) or not s['command'].strip():
        errs.append('server needs a command (or a url)')
    if not isinstance(s.get('args', []), list) or not all(isinstance(a, str) for a in s.get('args', [])):
        errs.append('server args must be a list of strings')
    env = s.get('env', {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        errs.append('server env must map names to strings')
    return errs


def public(spec: dict) -> dict:
    """A copy with every server.env value masked — what may leave the server."""
    out = copy.deepcopy(spec)
    env = (out.get('server') or {}).get('env') if isinstance(out.get('server'), dict) else None
    if isinstance(env, dict):
        out['server']['env'] = {k: mcp.mask(str(v)) for k, v in env.items()}
    return out


def _claude(server: dict) -> dict:
    if 'url' in server:
        return {'type': 'http', 'url': server['url']}
    out = {'command': server['command'], 'args': list(server.get('args') or [])}
    if server.get('env'):
        out['env'] = dict(server['env'])
    return out


def _held(name: str, server: dict, folder, run_id: str) -> dict:
    """A service card's command server, run through `eki mcp-hold` so using it holds the card."""
    env = dict(server.get('env') or {})
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    if os.environ.get('EKI_HOME'):
        env['EKI_HOME'] = os.environ['EKI_HOME']
    args = ['-m', 'eki', 'mcp-hold', '--run', run_id, '--dir', os.path.abspath(str(folder)), name, '--',
            server['command'], *list(server.get('args') or [])]
    return {'command': sys.executable, 'args': args, 'env': env}


def servers_of(folder, conn: Optional[sqlite3.Connection] = None,
               run_id: Optional[str] = None) -> Dict[str, dict]:
    """The project's mcp cards and service cards with "mcp" reachable from `folder`, by name,
    in Claude's format; with `run_id`, a service card's command server is wrapped to hold it."""
    try:
        root = project_root(folder, conn)
    except Exception:
        return {}
    if root is None:
        return {}
    found, _ = own(root)
    out = {}
    for c in found.values():
        if c.kind == 'mcp':
            out[c.name] = _claude(c.spec['server'])
        elif c.kind == 'service' and isinstance(c.spec.get('mcp'), dict):
            s = c.spec['mcp']
            out[c.name] = _held(c.name, s, folder, run_id) if run_id and 'url' not in s else _claude(s)
    return out


def _browser_slots(merged: Dict[str, dict]) -> Tuple[Dict[str, dict], bool]:
    """A copy where every stdio server that mentions playwright looks up its browsers in the shim;
    and whether any did."""
    out, hit = {}, False
    for name, spec in merged.items():
        said = ' '.join([str(spec.get('command') or '')] + [str(a) for a in spec.get('args') or []])
        if 'url' in spec or 'playwright' not in said.lower():
            out[name] = spec
            continue
        out[name] = {**spec, 'env': {**(spec.get('env') or {}),
                                     'PLAYWRIGHT_BROWSERS_PATH': str(browsershim.mirror())}}
        hit = True
    return out, hit


def run_config(folder, run_id: str, conn: Optional[sqlite3.Connection] = None
               ) -> Tuple[Optional[str], List[str]]:
    """(the file for Claude's --mcp-config or None, Codex's -c flags) for one run."""
    mine = servers_of(folder, conn, run_id)
    if not run_id or '/' in run_id or run_id.startswith('.'):
        return mcp.claude_config(), mcp.codex_config()
    merged, browsers = _browser_slots({**mcp.servers(), **mine})    # a project card wins a name clash
    if not mine and not browsers:
        return mcp.claude_config(), mcp.codex_config()
    d = paths.home() / 'mcp-runs'
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{run_id}.json'
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(json.dumps({'mcpServers': merged}, indent=2) + '\n')
    os.chmod(f, 0o600)
    return str(f), mcp_flags(merged)


def notes(comp: Component) -> List[str]:
    s = comp.spec.get('server') or {}
    return [f'url {s["url"]}' if 'url' in s else ' '.join([s.get('command', '')] + list(s.get('args') or []))]


def call(comp: Component, c: Call) -> Result:
    return Result(error=f'{comp.name} is a mcp card: it is not called with eki use')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False
