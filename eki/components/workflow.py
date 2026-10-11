"""The `workflow` kind: how this project's work runs — router, plan, parallel, checks, merge.

`command` is gate 1's check: text, or a list of path rules (eki/pathrules.py) so a change runs
only the checks for what it touched; `batch_command` is gate 2's, defaulting to a text command.

A passive card the engine reads through eki/workflow.py; at most one per project. `checks`
always holds `self`; `train` belongs to eki's own project only, and there the stages in
workflow.LOCKED can't be dropped: they protect eki itself.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from . import NAME, Call, Component, Result

ABOUT = "how work on this project goes: who picks it up, how it is split, checked and kept"
#: the kind's name and what it is, in plain words, for the person (kindtext.py)
TITLE = "Workflow"
EXPLAIN = ("How work on this project goes from a request to a finished change: who picks it up, whether it is "
           "split into smaller pieces, how many run at once, which checks it must pass and whether it is kept on "
           "its own or waits for you. Change it here to make the project's work faster or more careful.")

CHOICES = {'router': ['on', 'off'], 'plan': ['split', 'single'], 'merge': ['auto', 'pr', 'person']}
CHOICE_DEFAULTS = {'router': 'on', 'plan': 'split', 'merge': 'auto'}
CHOICE_HELP = {
    'router': {'on': "Requests are routed: rules first, a small model for the rest, among the providers "
                     "and the project's cards.",
               'off': 'Everything goes to target (provider → model → effort); the prompt check is never called.'},
    'plan': {'split': 'A planner turns a request into items with write-sets, built in parallel.',
             'single': 'The whole request is one item for one agent.'},
    'merge': {'auto': 'eki lands it on the branch.',
              'pr': 'A pull request on GitHub.',
              'person': 'Waits for the person to merge.'}}
#: a workflow card isn't a shared resource: the common `access` setting doesn't apply
DROPS = ('access',)
PARALLEL = 4
PLAYTESTER = 'playtester'


def _second(folder: Path) -> str:
    """The workflow card sorting before this one in the same components/, or ''."""
    try:
        others = sorted(d for d in folder.parent.iterdir() if d.is_dir() and d.name < folder.name)
    except OSError:
        return ''
    for d in others:
        try:
            spec = json.loads((d / 'component.json').read_text())
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(spec, dict) and spec.get('kind') == 'workflow':
            return d.name
    return ''


def _checks(spec: dict, eki: bool) -> List[str]:
    from ..workflow import CHECKS, LOCKED
    checks = spec.get('checks', ['self'])
    if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
        return [f'checks must be a list of {", ".join(CHECKS)}']
    bad = [c for c in checks if c not in CHECKS]
    if bad:
        return [f'checks: {", ".join(bad)} is not a check (checks: {", ".join(CHECKS)})']
    if len(set(checks)) != len(checks):
        return ['checks: each check once']
    if checks != sorted(checks, key=CHECKS.index):
        return [f'checks run in this order: {", ".join(CHECKS)}']
    errs = [] if 'self' in checks else ['checks: self is always on']
    if 'train' in checks and not eki:
        errs.append("checks: train is eki's own (build + drills + swap): only eki's project has it")
    if eki:
        errs += [f"{c} protects eki itself: it can't be turned off here" for c in LOCKED if c not in checks]
    return errs


def _command(spec: dict, checks: list, eki: bool) -> List[str]:
    """`command` (text, or path rules: eki/pathrules.py) and `batch_command`, gate 2's command."""
    from .. import pathrules
    cmd, batch = spec.get('command'), spec.get('batch_command')
    errs, rules = [], isinstance(cmd, list)
    if 'batch_command' in spec and not isinstance(batch, str):
        errs.append('batch_command must be text')
    if eki:   # eki's own gates are fixed: bin/check
        if rules:
            errs.append("command: eki's own check is bin/check; rules are for projects")
        elif 'command' in spec and not isinstance(cmd, str):
            errs.append('command must be text')
        if batch:
            errs.append("batch_command: eki's own gate 2 is bin/check")
        return errs
    if rules:
        try:
            pathrules.parse(cmd)
        except ValueError as e:
            errs.append(str(e) if str(e).startswith('command') else f'command: {e}')
    elif cmd is not None and not isinstance(cmd, str):
        errs.append('command must be text or a list of rules')
    text = isinstance(cmd, str) and bool(cmd.strip())
    if 'command' in checks and not (text or rules) or 'batch' in checks and not (text or rules or batch):
        errs.append('command is required for the command and batch checks: the project\'s check command')
    elif 'batch' in checks and rules and not (isinstance(batch, str) and batch.strip()):
        errs.append('batch_command is required for the batch check when command is a list of rules: '
                    'what runs on the changes together')
    return errs


def validate(spec: dict, folder: Path) -> List[str]:
    from .. import github, targets
    from ..workflow import is_eki
    errs = []
    for key, allowed in CHOICES.items():
        if spec.get(key, CHOICE_DEFAULTS[key]) not in allowed:
            errs.append(f'{key} must be one of {", ".join(allowed)}')
    n = spec.get('parallel', PARALLEL)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        errs.append('parallel must be a whole number ≥ 1')
    root = folder.parent.parent
    eki = is_eki(root)
    errs += _checks(spec, eki)
    checks = spec.get('checks', ['self']) if isinstance(spec.get('checks', []), list) else []
    errs += _command(spec, checks, eki)
    if 'after_merge' in spec and not isinstance(spec['after_merge'], str):
        errs.append('after_merge must be text')
    if 'playtester' in checks:
        p = spec.get('playtester', PLAYTESTER)
        if not isinstance(p, str) or not NAME.match(p):
            errs.append('playtester must be the name of a card')
    if spec.get('router', CHOICE_DEFAULTS['router']) == 'off':
        if not spec.get('target'):
            errs.append('target is required when router is off: where everything goes')
        else:
            try:
                targets.parse(spec['target'])
            except ValueError as e:
                errs.append(f'target: {e}')
    if spec.get('merge') == 'pr' and github.path_of(root)[0] is None:
        errs.append('merge: pr needs a GitHub repo')
    other = _second(folder)
    if other:
        errs.append(f'a project has one workflow card: {other} is it')
    return errs


def notes(comp: Component) -> List[str]:
    from .. import workflow
    return [workflow.said(workflow._of(comp))]


def call(comp: Component, c: Call) -> Result:
    return Result(error='a workflow card is read by the engine, not called')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False
