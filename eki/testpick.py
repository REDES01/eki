"""Which tests gate 1 runs for a change: a card's `tests` rules, else the tests that import a file.

A touched file under a tests rule draws that rule's test files (its patterns matched against the
files under root/tests, found by listing them). A touched file under no tests rule draws the
tests whose text imports it (`importers`, a plain text scan: nothing is imported or run). A rule
that holds the file only through a folder-wide pattern (`eki/**`, `eki/`) adds its tests on top
of that scan rather than standing in for it, so a card can run one test on every change. The
whole suite runs when a matching rule says "all", when more than `whole_over` files changed, or
when a module outside tests/ has no tests at all. Stdlib only.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import pathrules

TESTS = "tests"


@dataclass(frozen=True)
class Picked:
    whole: bool                 # run the whole suite
    files: Tuple[str, ...]      # repo-relative test files, sorted, each once
    because: Tuple[str, ...]    # the touched files that drew tests (or the reason for whole)


def _broad(pattern: str) -> bool:
    return pattern.endswith("/") or pattern.endswith("/**") or pattern == "**"


def _names_it(rule, path: str) -> bool:
    """Does `rule` hold `path` through a pattern narrower than a whole folder?"""
    return any(pathrules.matches(p, path) and not _broad(p) for p in rule.paths)


def _listed(root: Path) -> List[str]:
    """Every file under root/tests, repo-relative and `/`-separated, sorted."""
    out = []
    for here, dirs, files in os.walk(Path(root) / TESTS):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        rel = Path(here).relative_to(root).as_posix()
        out += [f"{rel}/{f}" for f in files]
    return sorted(out)


def _texts(root: Path) -> Dict[str, str]:
    """The text of every root/tests/**/*.py, by repo-relative path."""
    out = {}
    for f in _listed(root):
        if f.endswith(".py"):
            try:
                out[f] = (Path(root) / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith(TESTS + "/") and name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py"))


def _names(listed: str) -> List[str]:
    """The names an import list brings in: '(a, b as c,\\n d)' → [a, b, d]."""
    listed = re.sub(r"#[^\n]*", "", listed).replace("\\\n", " ").strip("() \t\n")
    return [n.split()[0] for n in (p.strip() for p in listed.split(",")) if n]


def _finder(path: str):
    """A test (text → bool) for "this text imports `path`"."""
    if not path.endswith(".py"):
        base = path.rsplit("/", 1)[-1]
        return lambda text: base in text
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return lambda text: False
    dotted = re.compile(r"(?<![\w.])" + re.escape(".".join(parts)) + r"(?!\w)")
    froms = None
    if len(parts) > 1:
        froms = re.compile(r"(?m)^\s*from\s+" + re.escape(".".join(parts[:-1])) +
                           r"\s+import\s+(\([^)]*\)|[^\n]*(?:\\\n[^\n]*)*)")

    def finds(text: str) -> bool:
        if dotted.search(text):
            return True
        return froms is not None and any(parts[-1] in _names(m.group(1)) for m in froms.finditer(text))
    return finds


def _importers(texts: Dict[str, str], path: str) -> List[str]:
    if _is_test(path):
        return [path]
    finds = _finder(path)
    return sorted(f for f, text in texts.items() if _is_test(f) and f != path and finds(text))


def importers(root: Path, path: str) -> List[str]:
    """The test files under root/tests whose text imports `path` (a repo-relative file): for
    `eki/a/b.py`, `eki.a.b` as a whole word or a `from eki.a import … b …` list; for any other
    file, its base name. A test file is its own importer."""
    return _importers(_texts(Path(root)), path)


def pick(rules, touched: List[str], root: Optional[Path], whole_over: int = 25) -> Picked:
    """The tests gate 1 runs for the `touched` files under these rules (only `tests` rules count)."""
    root = Path(root) if root is not None else Path(".")
    touched = list(dict.fromkeys(t for t in touched if t))
    if len(touched) > whole_over:
        return Picked(True, (), (f"{len(touched)} files changed",))
    tests = [r for r in rules if r.tests]
    hit = {t: pathrules.chosen(tests, [t]) for t in touched}
    core = [t for t in touched if any(r.tests == pathrules.ALL for r in hit[t])]
    if core:
        return Picked(True, (), tuple(core))
    named = {t: any(_names_it(r, t) for r in hit[t]) for t in touched}
    listed = _listed(root) if any(hit.values()) else []
    texts = _texts(root) if not all(named.values()) else {}
    files, because = set(), []
    for t in touched:
        got = [f for f in listed if any(pathrules.matches(p, f) for r in hit[t] for p in r.tests)]
        if not named[t]:
            scanned = _importers(texts, t)
            if not scanned and t.endswith(".py") and not t.startswith(TESTS + "/"):
                return Picked(True, (), (f"{t} has no tests",))
            got += scanned
        if got:
            files.update(got)
            because.append(t)
    return Picked(False, tuple(sorted(files)), tuple(because))


def line(picked: Picked, test_command: str) -> str:
    """Gate 1's shell text: the test command over the picked files (alone for the whole suite)."""
    if picked.whole:
        return test_command
    if not picked.files:
        return f"echo {shlex.quote(pathrules.NOTHING)}"
    return " ".join([test_command] + [shlex.quote(f) for f in picked.files])


def _few(names: Tuple[str, ...]) -> str:
    return ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")


def said(picked: Picked) -> str:
    """What gate 1 ran, for judged_by: 'gate 1: 4 test files for eki/queue.py'."""
    if picked.whole:
        return f"gate 1: whole fast suite ({_few(picked.because)})"
    if not picked.files:
        return "gate 1: no tests for these files"
    n = len(picked.files)
    return f"gate 1: {n} test file{'s' if n != 1 else ''} for {_few(picked.because)}"
