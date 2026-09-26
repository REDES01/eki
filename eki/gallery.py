"""The pictures eki has drawn, newest first — what the window's gallery and
`eki pictures` show.

Everything comes from the store: a picture is a `tool` event named "image"
with a `path`, joined to its run and thread. The files themselves live in
`~/.eki/images/<run>/` and are kept for good — housekeeping only forgets
journal rows, never anything under images/. A file removed by hand is simply
left out.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

#: how many events are read at a time while looking for pictures still on disk
_BATCH = 200


def pictures(conn: sqlite3.Connection, limit: int = 60,
             before: Optional[float] = None) -> List[Dict[str, Any]]:
    """Up to `limit` pictures still on disk, newest first; `before` (an event
    time) pages back past the ones already shown."""
    out: List[Dict[str, Any]] = []
    where, args = "", []                     # type: str, List[Any]
    if before is not None:
        where, args = " AND e.t < ?", [float(before)]
    while len(out) < limit:
        rows = conn.execute(
            "SELECT e.id, e.t, e.data, r.id AS run, r.thread_id, r.prompt, r.row, r.provider, "
            " t.title FROM events e JOIN runs r ON r.id = e.run_id "
            "LEFT JOIN threads t ON t.id = r.thread_id "
            "WHERE e.kind = 'tool' AND json_extract(e.data, '$.name') = 'image'" + where +
            " ORDER BY e.t DESC, e.id DESC LIMIT ?", (*args, _BATCH)).fetchall()
        for e in rows:
            path = json.loads(e["data"]).get("path")
            if path and os.path.isfile(path):
                out.append({"path": path, "run": e["run"], "thread": e["thread_id"],
                            "thread_title": e["title"] or "", "prompt": e["prompt"],
                            "row": e["row"], "provider": e["provider"], "created_at": e["t"]})
                if len(out) >= limit:
                    break
        if len(rows) < _BATCH:
            break
        last = rows[-1]                      # the next batch starts past the last one read
        where, args = " AND (e.t < ? OR (e.t = ? AND e.id < ?))", [last["t"], last["t"], last["id"]]
    return out
