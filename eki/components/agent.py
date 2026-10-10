"""The `agent` kind: a persona with prompt.md, called as a session (stub)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from . import Call, Component, Result

ABOUT = "a persona with its own prompt.md, skills and target, called as a session"


def validate(spec: dict, folder: Path) -> List[str]:
    if not (folder / 'prompt.md').is_file():
        return ['an agent needs prompt.md']
    return []


def call(comp: Component, c: Call) -> Result:
    return Result(error='agent calls are not built yet')


def is_up(comp: Component) -> bool:
    return False


def stop(comp: Component) -> bool:
    return False


def notes(comp: Component) -> List[str]:
    return []
