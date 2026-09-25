# SPDX-License-Identifier: Apache-2.0
"""Memory: a folder of plain markdown notes every harness shares
(ROADMAP Stage 7).

As simple as Claude Code's own client memory: one fact per file, a few
lines of frontmatter that say what it is and where it came from, and the
fact in plain words. Two folders — yours everywhere (`~/.eki/memory`) and
a project's (its `.eki/memory`, so it travels with the repo) — and nothing
else: no index to keep in step, no graph, no decay. You prune by editing
the folder.

`eki remember` / `eki recall` and the `eki_remember` / `eki_recall` tools
are the same calls over this module. Reading looks in the project first,
then yours; writing goes to the project when the call is made inside one.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import projects

#: your notes, for every folder
HOME = Path("~/.eki/memory").expanduser()
#: a project's, beside its routing.yaml
PROJECT = Path(".eki") / "memory"

SCOPES = ("", "project", "global")
#: a note's name becomes its file name; past this it's a sentence, not a name
NAME_MAX = 60


def slug(name: str) -> str:
    """A file name from a name or a first line: lowercase words and hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:NAME_MAX].rstrip("-")


def folders(where: str = "", scope: str = "") -> List[Tuple[str, Path]]:
    """The folders a call reads, nearest first: the project `where` is in,
    then yours. `scope` narrows it to one; asking for the project's outside
    one is a mistake worth saying."""
    if scope not in SCOPES:
        raise ValueError(f"scope is project or global, not {scope!r}")
    out: List[Tuple[str, Path]] = []
    if scope in ("", "project"):
        root = projects.find(where) if where else None
        if root is not None:
            out.append(("project", root / PROJECT))
        elif scope == "project":
            raise ValueError("not in a project (no .eki/ here or above) — "
                             "`eki project init` makes one, or leave the note global")
    if scope in ("", "global"):
        out.append(("global", HOME))
    return out


def _split(raw: str) -> Tuple[Dict[str, str], str]:
    """Frontmatter's top-level `key: value` lines, and the body. Nested
    keys (Claude's `metadata:` block) are read flat; anything else is body."""
    meta: Dict[str, str] = {}
    if not raw.startswith("---\n"):
        return meta, raw.strip()
    end = raw.find("\n---", 4)
    if end < 0:
        return meta, raw.strip()
    for line in raw[4:end].splitlines():
        m = re.match(r"\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2):
            meta.setdefault(m.group(1), m.group(2).strip().strip('"'))
    return meta, raw[end + 4:].strip()


def _note(scope: str, path: Path, body: bool = False) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return None
    meta, text = _split(raw)
    first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
    note: Dict[str, Any] = {"name": path.stem, "scope": scope, "path": str(path),
                            "description": meta.get("description") or first[:120],
                            "from": meta.get("from", ""), "updated": meta.get("updated", "")}
    if body:
        note["text"] = text
    return note


def listing(where: str = "", scope: str = "") -> List[Dict[str, Any]]:
    """Every note the call can see, the project's first, by name within each."""
    out: List[Dict[str, Any]] = []
    for label, folder in folders(where, scope):
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else ():
            note = _note(label, path)
            if note:
                out.append(note)
    return out


def read(name: str, where: str = "", scope: str = "") -> Optional[Dict[str, Any]]:
    """One note, whole — the project's when both have one by that name."""
    key = slug(name)
    if not key:
        return None
    for label, folder in folders(where, scope):
        path = folder / f"{key}.md"
        if path.is_file():
            return _note(label, path, body=True)
    return None


def search(query: str, where: str = "", scope: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """Notes holding every word of `query` (any case, name included), each
    with the lines that matched — enough to decide which to read."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    out: List[Dict[str, Any]] = []
    for label, folder in folders(where, scope):
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else ():
            note = _note(label, path, body=True)
            if not note:
                continue
            hay = (note["name"] + " " + note["description"] + "\n" + note["text"]).lower()
            if not all(w in hay for w in words):
                continue
            note["lines"] = [ln.strip() for ln in note.pop("text").splitlines()
                             if any(w in ln.lower() for w in words)][:3]
            out.append(note)
            if len(out) >= limit:
                return out
    return out


def write(text: str, name: str = "", where: str = "", scope: str = "",
          description: str = "", source: str = "", append: bool = False) -> Dict[str, Any]:
    """Keep a note: the project's when `where` is in one (or `scope` says
    so), yours otherwise. The same name replaces the note, or adds to it
    with `append`. What it says and where it came from go in its
    frontmatter, so a person reading the folder sees both."""
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to remember")
    first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
    key = slug(name or first)
    if not key:
        raise ValueError("a note needs a name made of letters or digits")
    label, folder = folders(where, scope)[0]
    path = folder / f"{key}.md"
    old = _note(label, path, body=True) if path.is_file() else None
    if append and old:
        text = old["text"].rstrip() + "\n\n" + text
        description = description or old["description"]
    description = " ".join((description or first).split())[:200]
    head = ["---", f"name: {key}", f"description: {description}"]
    if source or (old and old.get("from")):
        head.append(f"from: {source or old['from']}")
    head += [f"updated: {time.strftime('%Y-%m-%d %H:%M')}", "---", ""]
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head) + text + "\n")
    return {**(_note(label, path) or {}), "made": old is None}
