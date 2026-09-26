"""The docs-only lane: an item that touched only Markdown or docs/ needs no test run.

Its judge is `python -m eki.doccheck <base>..<sha>` in the item's worktree: every
changed path must be in the lane, and every added or changed file must be UTF-8
text. Stdlib only — it runs under a bare interpreter.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from typing import Iterable, List, Optional, Tuple


def _in_lane(path: str) -> bool:
    path = path[2:] if path.startswith("./") else path
    return path.endswith(".md") or path.startswith("docs/")


def docs_only(files: Iterable[str]) -> bool:
    """True when `files` is non-empty and every path is Markdown or under docs/."""
    files = list(files)
    return bool(files) and all(_in_lane(f) for f in files)


def of_item(it: sqlite3.Row) -> bool:
    return docs_only(json.loads(it["touched"] or "[]"))


def command(base: str, sha: str) -> str:
    """The prompt of the provider='command' judge run for a docs-only item."""
    script = f'PYTHONPATH=$PWD "${{EKI_PYTHON:-python3}}" -m eki.doccheck {base}..{sha}'
    return json.dumps(["/bin/sh", "-c", script])


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], capture_output=True)


def _changes(base: str, sha: str) -> Optional[List[Tuple[str, str]]]:
    """(status letter, path) per changed file, or None when the range can't be read."""
    out = _git("diff", "--name-status", "--no-renames", "-z", base, sha, "--")
    if out.returncode != 0:
        return None
    parts = out.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [(parts[i][:1], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def _is_text(sha: str, path: str) -> bool:
    blob = _git("show", f"{sha}:{path}")
    if blob.returncode != 0:
        return False
    try:
        blob.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or ".." not in argv[0]:
        print("usage: python -m eki.doccheck <base>..<sha>", file=sys.stderr)
        return 1
    base, sha = argv[0].split("..", 1)
    changes = _changes(base, sha)
    if changes is None:
        print(f"✗ cannot read the range {base}..{sha}")
        return 1
    if not changes:
        print(f"✗ nothing changed in {base}..{sha}")
        return 1
    ok = True
    for status, path in changes:
        if not _in_lane(path):
            print(f"✗ {path}: outside the docs lane")
            ok = False
            continue
        if status != "D" and not _is_text(sha, path):
            print(f"✗ {path}: not UTF-8 text")
            ok = False
            continue
        print(f"✓ {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
