"""The `service` kind: a command per call, or a server kept up while used (stub)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from . import Call, Component, Result

ABOUT = "a command run per call, or a server kept up while it's used"


def validate(spec: dict, folder: Path) -> List[str]:
    if spec.get('run') or spec.get('provider'):
        return []
    missing = [k for k in ('serve', 'health', 'call') if not spec.get(k)]
    if 'serve' in spec and missing:
        return [f'a served component needs serve, health and call (missing {", ".join(missing)})']
    if missing:
        return ['a service needs "run", or "serve" + "health" + "call", or "provider"']
    return []


def call(comp: Component, c: Call) -> Result:
    return Result(error='service calls are not built yet')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    return []
