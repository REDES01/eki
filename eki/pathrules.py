"""Path rules: a workflow card's `command` as a list of rules, so gate 1 runs only what a change touched.

A rule is {"name": optional, "paths": [patterns], "run": command}, or the same with "tests" in
place of "run": test-file patterns under the project's tests/ folder, or "all" for the whole
suite (eki/testpick.py picks them). Patterns follow .gitignore:
no `/` (but a trailing one) matches a name at any depth; a `/` anchors to the project root (a
leading one is dropped); `*` and `?` stay in one folder; `**/` is zero or more folders; a
trailing `/**` or `/` is everything under that folder. `!` patterns aren't supported.
Stdlib only.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List, Tuple

NOTHING = "nothing to check for these files"
KEYS = ("name", "paths", "run", "tests")
#: a tests rule's `tests` when it asks for the whole suite
ALL = ("all",)


@dataclass(frozen=True)
class Rule:
    paths: Tuple[str, ...]
    run: str               # '' for a tests rule
    name: str = ""
    tests: Tuple[str, ...] = ()   # test-file patterns; ALL is the whole suite


def _glob(text: str) -> str:
    """The regex for one pattern body: `*`/`?` within a folder, `[...]` classes, `\\x` literal."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "*":
            while i + 1 < n and text[i + 1] == "*":
                i += 1
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(text[i]))
        elif c == "[":
            k = i + 1
            k += 1 if text[k:k + 1] in ("!", "^") and k < n else 0
            k += 1 if text[k:k + 1] == "]" else 0
            j = text.find("]", k)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = text[i + 1:j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("(?!/)[" + body.replace("\\", "\\\\").replace("[", "\\[") + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@lru_cache(maxsize=1024)
def _regex(pattern: str) -> "re.Pattern[str]":
    p = pattern
    under = p.endswith("/")
    p = p.rstrip("/")
    anchored = "/" in p
    p = p.lstrip("/")
    parts = p.split("/")
    rx, tail = [], ""
    if parts and parts[-1] == "**" and len(parts) > 1:   # `x/**`: everything under x
        parts, tail = parts[:-1], "/.*"
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        if part == "**" and not last:
            rx.append("(?:[^/]+/)*")
        elif part == "**":                                  # a lone `**`: anything
            rx.append(".*")
        else:
            rx.append(_glob(part) + ("" if last else "/"))
    head = "" if anchored else "(?:.*/)?"
    end = tail or ("/.*" if under else "")
    return re.compile(head + "".join(rx) + end + r"\Z")


def matches(pattern: str, path: str) -> bool:
    """`path` (relative to the project root, `/`-separated) matches the .gitignore-style pattern."""
    if not pattern or pattern.startswith("!"):
        return False
    return _regex(pattern).match(path.lstrip("/")) is not None


def parse(value) -> Tuple[Rule, ...]:
    """The rules of a card's `command` list; ValueError (words starting command/paths) when wrong."""
    if not isinstance(value, list):
        raise ValueError("command must be text or a list of rules")
    if not value:
        raise ValueError("command needs at least one rule")
    rules = []
    for n, r in enumerate(value, 1):
        if not isinstance(r, dict):
            raise ValueError(f"command: rule {n} must be an object with paths and run (or tests)")
        extra = sorted(set(r) - set(KEYS))
        if extra:
            raise ValueError(f"command: rule {n} has unknown keys: {', '.join(extra)} (a rule has {', '.join(KEYS)})")
        name = r.get("name", "")
        if not isinstance(name, str):
            raise ValueError(f"command: rule {n}'s name must be text")
        paths = r.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
            raise ValueError(f"paths: rule {n} needs paths, a list of non-empty text")
        if any(p.strip().startswith("!") for p in paths):
            raise ValueError("paths: `!` patterns aren't supported")
        if "run" in r and "tests" in r:
            raise ValueError(f"command: rule {n} has both run and tests (a rule has one of them)")
        if "tests" in r:
            rules.append(Rule(paths=tuple(p.strip() for p in paths), run="", name=name.strip(),
                              tests=_tests(n, r["tests"])))
            continue
        run = r.get("run")
        if not isinstance(run, str) or not run.strip():
            raise ValueError(f"command: rule {n} needs run, the command to run (or tests, the test files)")
        rules.append(Rule(paths=tuple(p.strip() for p in paths), run=run, name=name.strip()))
    return tuple(rules)


def _tests(n: int, value) -> Tuple[str, ...]:
    """A rule's `tests`: "all" is ALL, else a non-empty list of non-empty patterns."""
    if isinstance(value, str) and value.strip() == "all":
        return ALL
    if not isinstance(value, list) or not value or not all(isinstance(p, str) and p.strip() for p in value):
        raise ValueError(f"command: rule {n}'s tests must be \"all\" or a list of test-file patterns")
    if any(p.strip().startswith("!") for p in value):
        raise ValueError("paths: `!` patterns aren't supported")
    return tuple(p.strip() for p in value)


def label(rule: Rule) -> str:
    """The rule's name, else its paths joined with ', '."""
    return rule.name or ", ".join(rule.paths)


def chosen(rules: Iterable[Rule], touched: Iterable[str]) -> List[Rule]:
    """The rules with a path matching any touched file: card order, each once."""
    files = [t for t in touched if t]
    return [r for r in rules if any(matches(p, f) for p in r.paths for f in files)]


def script(rules: Iterable[Rule]) -> str:
    """The shell text running every `run` rule, each under `== <label>: <run>`, red or not; it
    exits non-zero when any was red. No such rules: it only prints NOTHING (and exits 0)."""
    rules = [r for r in rules if r.run]
    if not rules:
        return f"echo {shlex.quote(NOTHING)}"
    lines = ["s=0"]
    for r in rules:
        lines.append(f"printf '%s\\n' {shlex.quote(f'== {label(r)}: {r.run}')}")
        lines.append(f"( {r.run}\n) || s=1")
    lines.append("exit $s")
    return "\n".join(lines)
