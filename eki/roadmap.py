"""ROADMAP.md as a list of open entries (docs/self-build.md, "The loop:
eki picks its own work").

The picker's last stage ranks what is left here. An entry is a `- [ ]`
line plus its indented continuation lines; done `- [x]` entries, entries
marked *(for a person)* and everything under `## Not planned` are never
taken. The key is a hash of the normalised text, so an edited entry is a
new entry; once a goal carries `roadmap:<key>` in `pick_key` the entry is
not eligible again, however that goal ended.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Set

from . import integration
from .workspace import WorkspaceError, git

SKIP_SECTION = "not planned"
FOR_PERSON = "(for a person"
TITLE_MAX = 100

_BOX = re.compile(r"^- \[( |x|X)\]\s?(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


@dataclass(frozen=True)
class Entry:
    key: str
    section: str
    title: str
    text: str


def key_of(text: str) -> str:
    norm = re.sub(r"[*`]", "", text.lower())
    norm = " ".join(norm.split())
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


def _entry(section: str, lines: List[str]) -> Optional[Entry]:
    text = "\n".join(lines).strip()
    if not text or FOR_PERSON in text.lower():
        return None
    bold = _BOLD.search(" ".join(text.split()))
    title = bold.group(1).strip() if bold else lines[0].strip()
    return Entry(key=key_of(text), section=section, title=title[:TITLE_MAX], text=text)


def open_entries(text: str) -> List[Entry]:
    """The open `- [ ]` entries of a ROADMAP.md text, in file order."""
    out: List[Entry] = []
    section = ""
    lines: Optional[List[str]] = None          # the open entry being read

    def close():
        nonlocal lines
        if lines is not None and section.lower() != SKIP_SECTION:
            e = _entry(section, lines)
            if e:
                out.append(e)
        lines = None

    for raw in text.splitlines():
        if lines is not None and raw[:1] in (" ", "\t") and raw.strip():
            lines.append(raw.strip())
            continue
        close()
        if raw.startswith("## "):
            section = raw[3:].strip()
            continue
        m = _BOX.match(raw)
        if m and m.group(1) == " ":
            lines = [m.group(2)]
    close()
    return out


def read_main() -> str:
    """ROADMAP.md as it is on main in the integration repo; "" if it has none."""
    try:
        return git(integration.repo(), "show", "main:ROADMAP.md")
    except WorkspaceError:
        return ""


def picked_keys(conn) -> Set[str]:
    """Every roadmap key a goal was opened for, whatever became of the goal."""
    rows = conn.execute("SELECT pick_key FROM goals WHERE pick_key LIKE 'roadmap:%'").fetchall()
    return {r[0][len("roadmap:"):] for r in rows}


def eligible(conn, entries: List[Entry]) -> List[Entry]:
    taken = picked_keys(conn)
    return [e for e in entries if e.key not in taken]
