"""The `assets` kind: a folder eki manages outside git, seen by every worktree (stub)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from . import Call, Component, Result

ABOUT = "a folder of files managed outside git, shared by every worktree of the project"


def validate(spec: dict, folder: Path) -> List[str]:
    p = spec.get('path')
    if not isinstance(p, str) or not p.strip():
        return ['assets need "path": a folder relative to the project']
    if Path(p).is_absolute() or '..' in Path(p).parts:
        return [f'path {p!r} must be relative, with no ".."']
    return []


def call(comp: Component, c: Call) -> Result:
    return Result(error='assets calls are not built yet')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    return []
