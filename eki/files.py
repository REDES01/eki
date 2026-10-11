"""Files an agent made or changed, shown in the thread.

eki only ever serves a file a run pointed at — one a tool wrote or edited,
or a path an answer named that exists, or a file sent with a request — or a page of eki's own daily digest,
or a file of the person's own the side panel may read (eki/docs.py: under home, no credentials,
not eki's internals) — never an arbitrary path. HTML is
served sandboxed, so a page an agent wrote can't act as eki.
"""
from __future__ import annotations

import mimetypes
import os
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from . import paths

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
    """A path a tool event named, or one a run was sent as an attachment."""
    row = conn.execute("SELECT 1 FROM events WHERE kind='tool' AND json_extract(data, '$.path') = ? LIMIT 1",
                       (path,)).fetchone()
    if row is None:
        row = conn.execute("SELECT 1 FROM runs, json_each(CASE WHEN json_valid(runs.attachments) "
                           "THEN runs.attachments ELSE '[]' END) AS a WHERE a.value = ? LIMIT 1", (path,)).fetchone()
    return row is not None


def digest_page(path: str) -> bool:
    """A .md file directly in EKI_HOME/self/digests — resolved, so '..' can't escape."""
    folder = (paths.home() / "self" / "digests").resolve()
    real = Path(path).resolve()
    return real.suffix == ".md" and real.parent == folder


def _doc_readable(conn: sqlite3.Connection, path: str) -> bool:
    from . import docs                     # docs imports this module
    if not path.lower().endswith(SHOWN):
        return False
    try:
        docs.policy(conn, path)
    except ValueError:
        return False
    return True


def read(conn: sqlite3.Connection, path: str) -> Optional[tuple]:
    """(bytes, content type) for a file a run pointed at, a digest page, or one the doc policy
    lets the window read (docs.policy: the person's own files under home), else None."""
    if not path or not os.path.isfile(path):
        return None
    if not (known(conn, path) or digest_page(path) or _doc_readable(conn, path)):
        return None
    if os.path.getsize(path) > 50 * 1024 * 1024:
        return None
    ctype = mimetypes.guess_type(path)[0] or "text/plain"
    if path.endswith((".md", ".mermaid", ".mmd", ".csv", ".json", ".txt")):
        ctype = "text/plain; charset=utf-8"
    with open(path, "rb") as f:
        return f.read(), ctype
