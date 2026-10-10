"""The `mcp` kind: an MCP server only this project's runs get.

`"server"` is `{"command", "args", "env"}` (stdio) or `{"url"}`. Nothing goes into eki's
registry (~/.eki/mcp.json) or a program's own config: a project run gets its cards' servers
merged with the registry for that run only. Env values never leave whole: `public()` masks them.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import mcp, paths
from ..providers.codex import mcp_flags
from . import Call, Component, Result, own, project_root

ABOUT = "an MCP server only this project's runs get"


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


def servers_of(folder, conn: Optional[sqlite3.Connection] = None) -> Dict[str, dict]:
    """The project's mcp cards reachable from `folder`, by name, in Claude's format."""
    try:
        root = project_root(folder, conn)
    except Exception:
        return {}
    if root is None:
        return {}
    found, _ = own(root)
    return {c.name: _claude(c.spec['server']) for c in found.values() if c.kind == 'mcp'}


def run_config(folder, run_id: str, conn: Optional[sqlite3.Connection] = None
               ) -> Tuple[Optional[str], List[str]]:
    """(the file for Claude's --mcp-config or None, Codex's -c flags) for one run."""
    mine = servers_of(folder, conn)
    if not mine or not run_id or '/' in run_id or run_id.startswith('.'):
        return mcp.claude_config(), mcp.codex_config()
    merged = {**mcp.servers(), **mine}            # a project card wins a name clash
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
