"""Files an agent made or changed, shown in the thread.

eki only ever serves a file a run pointed at — one a tool wrote or edited,
or a path an answer named that exists — never an arbitrary path. HTML is
served sandboxed, so a page an agent wrote can't act as eki.
"""
from __future__ import annotations

import mimetypes
import os
import re
import sqlite3
from typing import List, Optional

SHOWN = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".html", ".htm", ".md", ".pdf", ".txt",
         ".json", ".csv", ".mermaid", ".mmd")
_PATH = re.compile(r"(~?/[^\s`'\"()<>\[\]]+\.(?:" + "|".join(e[1:] for e in SHOWN) + r"))\b", re.I)


def named_in(text: str) -> List[str]:
    """Paths an answer names that are files on this Mac."""
    found = []
    for m in _PATH.finditer(text or ""):
        path = os.path.expanduser(m.group(1).rstrip(".,;:"))
        if os.path.isfile(path) and path not in found:
            found.append(path)
    return found[:12]


def known(conn: sqlite3.Connection, path: str) -> bool:
    row = conn.execute("SELECT 1 FROM events WHERE kind='tool' AND json_extract(data, '$.path') = ? LIMIT 1",
                       (path,)).fetchone()
    return row is not None


def read(conn: sqlite3.Connection, path: str) -> Optional[tuple]:
    """(bytes, content type) for a file a run pointed at, else None."""
    if not path or not known(conn, path) or not os.path.isfile(path):
        return None
    if os.path.getsize(path) > 50 * 1024 * 1024:
        return None
    ctype = mimetypes.guess_type(path)[0] or "text/plain"
    if path.endswith((".md", ".mermaid", ".mmd", ".csv", ".json", ".txt")):
        ctype = "text/plain; charset=utf-8"
    with open(path, "rb") as f:
        return f.read(), ctype
