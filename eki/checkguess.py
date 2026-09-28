"""Guess a project's check from the files in its clone.

Pure: it looks at a folder and says what command would judge a change
there, what installs its dependencies once, and why. The first rule that
matches wins; nothing matched is said too, never silently skipped.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PYTEST_VENV = ".venv/bin/python -m pytest -q"
VENV = "python3 -m venv .venv && .venv/bin/pip install -q"

# lockfile -> (manager, install), in the order they are looked for.
LOCKS = [
    ("pnpm-lock.yaml", "pnpm", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn", "yarn install --frozen-lockfile"),
    ("bun.lockb", "bun", "bun install --frozen-lockfile"),
    ("bun.lock", "bun", "bun install --frozen-lockfile"),
    ("package-lock.json", "npm", "npm ci"),
]


@dataclass(frozen=True)
class Guess:
    check: Optional[str]
    install: Optional[str]
    why: str


def guess(folder: Path) -> Guess:
    folder = Path(folder)
    for rule in (_bin_check, _node, _python, _make, _cargo, _go):
        g = rule(folder)
        if g:
            return g
    return Guess(None, None, "no check found")


def _bin_check(f: Path) -> Optional[Guess]:
    p = f / "bin" / "check"
    if p.is_file() and os.access(p, os.X_OK):
        return Guess("bin/check", None, "bin/check")
    return None


def _node(f: Path) -> Optional[Guess]:
    p = f / "package.json"
    if not p.is_file():
        return None
    try:
        scripts = json.loads(p.read_text()).get("scripts") or {}
    except (ValueError, OSError, AttributeError):
        return None
    if not isinstance(scripts, dict):
        return None
    script = next((s for s in ("check", "test", "build") if s in scripts), None)
    if not script:
        return None
    manager, install = "npm", "npm install"
    for lock, m, i in LOCKS:
        if (f / lock).exists():
            manager, install = m, i
            break
    return Guess(f"{manager} run {script}", install,
                 f"package.json script {script} via {manager}")


def _python(f: Path) -> Optional[Guess]:
    if not any((f / n).exists() for n in ("pyproject.toml", "pytest.ini")) \
            and not (f / "tests").is_dir():
        return None
    reqs = (f / "requirements.txt").is_file()
    if (f / "uv.lock").is_file():
        check, install, why = PYTEST_VENV, "uv sync", "pytest via uv"
    elif (f / "pyproject.toml").is_file() or (f / "setup.py").is_file():
        install = f"{VENV} -e . pytest" + (" -r requirements.txt" if reqs else "")
        check, why = PYTEST_VENV, "pytest via pip"
    elif reqs:
        install = f"{VENV} pytest -r requirements.txt"
        check, why = PYTEST_VENV, "pytest via pip requirements.txt"
    else:
        check, install, why = "python3 -m pytest -q", None, "pytest"
    if (f / "src").is_dir():
        check, why = "PYTHONPATH=src " + check, why + ", src layout"
    return Guess(check, install, why)


def _make(f: Path) -> Optional[Guess]:
    p = f / "Makefile"
    if not p.is_file():
        return None
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for target in ("check", "test"):
        if any(line.startswith(target + ":") for line in lines):
            return Guess(f"make {target}", None, f"Makefile target {target}")
    return None


def _cargo(f: Path) -> Optional[Guess]:
    if (f / "Cargo.toml").is_file():
        return Guess("cargo test", None, "Cargo.toml")
    return None


def _go(f: Path) -> Optional[Guess]:
    if (f / "go.mod").is_file():
        return Guess("go test ./...", None, "go.mod")
    return None
