# SPDX-License-Identifier: Apache-2.0
"""A project's roster and policy: who works here, and who does what
(ROADMAP Stage 5, docs/routing.md).

A project is a folder with `.eki/` in it. Its `.eki/routing.yaml` says,
for work done in that folder, which backends are on the roster and which
goes first for a kind of work:

    roster: [claude_code, codex]   # only these are chosen automatically here
    prose: claude_code             # writing, translation
    code: codex                    # code changes, big and small

It lives in the project, not in ~/.eki, so it travels with the repo and
reads the same for everyone who works in it. It sits on top of your own
table: a row the project sets goes before yours, and a rule for a thread
still goes before the project's.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

FILE = Path(".eki") / "routing.yaml"

#: words for a kind of work → the table's rows (any row id works too)
KINDS: Dict[str, List[str]] = {
    "prose": ["writing"], "writing": ["writing"],
    "code": ["code", "code_hard"],
    "quick": ["quick"], "explain": ["explain"], "code_hard": ["code_hard"],
    "retry": ["retry"], "research": ["research"],
    "picture": ["picture"], "pictures": ["picture"], "images": ["picture"],
}


def root(folder: str) -> Optional[Path]:
    """The nearest folder up from `folder` with `.eki/` in it — never your
    home folder, whose `.eki` is eki's own."""
    if not folder:
        return None
    try:
        start = Path(folder).expanduser().resolve()
        home = Path.home().resolve()
    except OSError:
        return None
    for d in (start, *start.parents):
        if d == home or d == Path(d.anchor):
            return None
        if (d / ".eki").is_dir():
            return d
    return None


def _targets(v: Any) -> List[str]:
    items = v if isinstance(v, list) else [v]
    return [str(x).strip() for x in items if x is not None and str(x).strip()]


def load(folder: str) -> Optional[Dict[str, Any]]:
    """{"root", "path", "roster", "rows": {row: [targets]}, "said": {row: words}}
    for the project `folder` is in, or None when it has no routing.yaml.
    A file that doesn't read is reported, not guessed at."""
    r = root(folder)
    path = r / FILE if r else None
    if path is None or not path.is_file():
        return None
    out: Dict[str, Any] = {"root": str(r), "path": str(path), "roster": [], "rows": {},
                           "said": {}, "problem": ""}
    try:
        import yaml
        raw = yaml.safe_load(path.read_text()) or {}
    except Exception as e:                          # noqa: BLE001
        out["problem"] = f"{path} doesn't read: {e}"[:300]
        return out
    if not isinstance(raw, dict):
        out["problem"] = f"{path} should be a list of `kind: backend` lines"
        return out
    out["roster"] = _targets(raw.get("roster") or [])
    for word, value in raw.items():
        rows = KINDS.get(str(word).lower())
        if not rows:
            continue
        ts = _targets(value)
        for row in rows:
            if ts:
                out["rows"][row] = ts
                out["said"][row] = f"{word}: {', '.join(ts)}"
    return out


def known_only(proj: Dict[str, Any], keys: List[str]) -> List[str]:
    """Drop the names in a project's file that aren't backends on this Mac
    (a typo, or one this Mac doesn't have), and return them to say so."""
    from eki.table import parse_target
    unknown: List[str] = []
    ok = lambda t: parse_target(t)[0] in keys          # noqa: E731
    for t in proj.get("roster") or []:
        if not ok(t):
            unknown.append(t)
    proj["roster"] = [t for t in proj.get("roster") or [] if ok(t)]
    for row, ts in list((proj.get("rows") or {}).items()):
        unknown += [t for t in ts if not ok(t) and t not in unknown]
        proj["rows"][row] = [t for t in ts if ok(t)]
    return unknown
