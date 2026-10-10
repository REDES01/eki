"""The `agent` kind: a persona with prompt.md, called as a session.

An agent folder holds prompt.md, optional skills/, and in component.json a
`target` (provider → model → effort, e.g. "claude:opus@high"), `bare`,
`uses` (components it may call), `mcp` (servers from eki's registry),
`deny_tools`, `allow_tools` (a bare agent's whole tool list) and an optional `url` ("{url}" in prompt.md becomes it).
An agent with a `url` (the playtester) plays the caller's own serving link
when the caller's project has a serve card; its `url` is only the fallback.

Each call is a new session under ~/.eki/components/sessions/<name>/. A bare
agent is strict and Claude Code only: prompt.md is its entire system prompt,
no setting sources, no skills, no built-in tools, only the listed MCP
servers, cwd an empty scratch folder outside any project. A non-bare agent
gets prompt.md as its note and its skills/ beside eki's, in the caller's folder.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import Call, Component, Result, NAME, journal

ABOUT = "a persona with its own prompt.md, skills and target, called as a session"


def _names(spec: dict, key: str) -> List[str]:
    v = spec.get(key, [])
    return v if isinstance(v, list) and all(isinstance(n, str) and n for n in v) else None


def _tool_list(spec: dict, key: str) -> List[str]:
    """The `mcp__<server>__<tool>` names of a listed server; empty when the list is broken."""
    tools = spec.get(key, [])
    servers = spec.get('mcp') or []
    ok = isinstance(tools, list) and all(
        isinstance(t, str) and t.startswith('mcp__') and t.split('__')[1] in servers and len(t.split('__')) == 3
        for t in tools)
    return list(tools) if ok else []


def validate(spec: dict, folder: Path) -> List[str]:
    errs = []
    if not (folder / 'prompt.md').is_file():
        errs.append('an agent needs prompt.md')
    try:
        t = target(spec)
    except ValueError as e:
        errs.append(f'target: {e}')
        t = None
    if not isinstance(spec.get('bare', False), bool):
        errs.append('bare must be true or false')
    for key in ('uses', 'mcp', 'deny_tools'):
        if _names(spec, key) is None:
            errs.append(f'{key} must be a list of names')
    if _names(spec, 'uses') and not all(NAME.match(n) for n in spec['uses']):
        errs.append('uses must name components')
    if 'url' in spec and not isinstance(spec['url'], str):
        errs.append('url must be text')
    if 'allow_tools' in spec and not _tool_list(spec, 'allow_tools'):
        errs.append('allow_tools must be a list of mcp__<server>__<tool> names of servers in "mcp"')
    if 'allow_tools' in spec and not spec.get('bare'):
        errs.append('allow_tools needs "bare": true')
    if spec.get('bare') and t is not None:
        kind = _kind(t.provider)
        if kind and kind != 'claude_code':   # a provider this Mac doesn't have fails at call time
            errs.append(f'a bare agent runs on Claude Code only (target {t.provider!r} is {kind})')
    return errs


def target(spec: dict):
    """The agent's Target; text may carry an effort after '@' ('claude:opus@high')."""
    from .. import targets
    x = spec.get('target', 'claude')
    if isinstance(x, str) and '@' in x:
        head, _, effort = x.rpartition('@')
        provider, _, model = head.partition(':')
        x = {'provider': provider, 'effort': effort, **({'model': model} if model else {})}
    return targets.parse(x)


def _kind(provider: str) -> str:
    from .. import providers
    try:
        entry = providers.config().get(provider) or providers.DEFAULTS.get(provider) or {}
        return str(entry.get('kind') or '')
    except Exception:
        return ''


def prompt_text(comp: Component, url: Optional[str] = None) -> str:
    text = (comp.folder / 'prompt.md').read_text()
    return text.replace('{url}', str(comp.spec.get('url') or '') if url is None else url)


def play_url(comp: Component, c: Call) -> str:
    """What an agent with a `url` plays: the caller's serving link, else its own `url`."""
    own = str(comp.spec.get('url') or '')
    if 'url' not in comp.spec:
        return own
    from . import serve
    return serve.for_run(c.caller_dir, by=c.caller_run or c.run_id) or own


def mcp_file(comp: Component, session: Path) -> Path:
    """session/mcp.json holding only the servers the agent lists, on or off in the registry."""
    from .. import mcp
    known = {**mcp.off(), **mcp.servers()}
    chosen: Dict[str, Any] = {}
    for name in comp.spec.get('mcp') or []:
        if name not in known:
            raise KeyError(f'no MCP server {name!r} in eki\'s registry')
        spec = json.loads(json.dumps(known[name]))
        spec['args'] = [str(a).replace('{session}', str(session)) for a in spec.get('args') or []]
        if not spec['args'] and 'url' in spec:
            spec.pop('args')
        chosen[name] = spec
    f = session / 'mcp.json'
    f.write_text(json.dumps({'mcpServers': chosen}, indent=2) + '\n')
    return f


def _skills_plugin(comp: Component, session: Path):
    """A plugin folder for the agent's own skills/ (None when it has none)."""
    skills = comp.folder / 'skills'
    if not skills.is_dir():
        return None
    root = session / 'plugin'
    (root / '.claude-plugin').mkdir(parents=True, exist_ok=True)
    (root / '.claude-plugin' / 'plugin.json').write_text(json.dumps(
        {'name': f'eki-{comp.name}', 'version': '1.0.0', 'description': f'Skills of the {comp.name} agent'}))
    (root / 'skills').symlink_to(skills.resolve(), target_is_directory=True)
    return str(root)


def turn_for(comp: Component, c: Call, session: Path, url: Optional[str] = None):
    """(provider name, the Turn) for one call of `comp`; `url` is what "{url}" becomes (play_url by default)."""
    from .. import providers, skills, targets
    from ..providers.base import Turn
    t = target(comp.spec)
    cfg = providers.config().get(t.provider) or {}
    choice = targets.choose(t, cfg, None)
    extra = {k: v for k, v in (c.extra or {}).items()
             if k not in ('bare', 'system_prompt', 'deny_tools', 'allowed_tools', 'model', 'effort', 'note', 'row', 'full_history')}
    if choice.model:
        extra['model'] = choice.model
    if choice.effort:
        extra['effort'] = choice.effort
    prompt = ' '.join(c.args) or 'Begin.'
    url = play_url(comp, c) if url is None else url
    if comp.spec.get('bare'):
        scratch = session / 'scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        for k in ('claude_plugin', 'claude_mcp', 'codex_config', 'codex_skills'):
            extra.pop(k, None)              # nothing of eki's own: only what the agent lists
        extra.update(bare=True, system_prompt=prompt_text(comp, url), deny_tools=list(comp.spec.get('deny_tools') or []))
        extra.update(tool_rules(comp.spec))
        if comp.spec.get('mcp'):
            extra['claude_mcp'] = str(mcp_file(comp, session))
        cwd = str(scratch)
    else:
        extra['note'] = prompt_text(comp, url)
        if comp.spec.get('deny_tools'):
            extra['deny_tools'] = list(comp.spec['deny_tools'])
        from . import skillcard
        project = skillcard.claude_plugin(skillcard.cards_for(c.caller_dir))
        # eki's store first, then the project's card, then the agent's own skills/
        plugins = [p for p in (skills.claude_plugin(), project, _skills_plugin(comp, session)) if p]
        if plugins:
            extra['claude_plugin'] = plugins
        if comp.spec.get('mcp'):
            extra['claude_mcp'] = str(mcp_file(comp, session))
        cwd = c.caller_dir
    return t.provider, Turn(prompt=prompt, history=[], cwd=cwd, run_id=c.run_id, extra=extra)


def tool_rules(spec: dict) -> Dict[str, Any]:
    """With `allow_tools` a bare agent gets exactly those tools: Claude Code runs in
    dontAsk mode, so everything not on the list — the rest of its servers, tools a
    server adds later — is refused, never asked about."""
    allow = _tool_list(spec, 'allow_tools') if spec.get('bare') else []
    return {'allowed_tools': allow} if allow else {}


def call(comp: Component, c: Call) -> Result:
    from .. import providers
    session = journal.new_session(comp.name)
    try:
        url = play_url(comp, c)
        name, turn = turn_for(comp, c, session, url)
        provider = providers.get(name)
    except (KeyError, ValueError, OSError) as e:
        return Result(text='', paths=[str(session)], error=str(e))
    texts: List[str] = []
    calls: List[dict] = []

    def emit(kind: str, data: dict) -> None:
        if kind == 'text' and data.get('text'):
            texts.append(data['text'])
        if kind == 'tool':
            calls.append(data)
        if c.emit:
            c.emit(kind, data)

    out = provider.take(turn, emit)
    kept = journal.keep(session, texts, calls, url)
    if out.state != 'done' or out.error:
        kept.error = out.error or out.reason or f'{comp.name} ended {out.state}'
    return kept


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    from .. import targets
    try:
        out = [f'target {targets.key(target(comp.spec))}']
    except ValueError as e:
        out = [f'target: {e}']
    if comp.spec.get('bare'):
        out.append('bare: only its own prompt')
    if comp.spec.get('uses'):
        out.append('uses ' + ', '.join(comp.spec['uses']))
    if comp.spec.get('mcp'):
        out.append('mcp ' + ', '.join(comp.spec['mcp']))
    return out
