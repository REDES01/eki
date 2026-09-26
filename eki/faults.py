"""Faults become items (docs/self-build.md, "Items").

A traceback in eki's own code that the journal (eki/observe.py) saw twice
within a day — same file:line, same exception — opens a goal of its own:
source 'fault', owner 'eki', one item "fix this" whose write-set is the file
in the top frame and its test file. Owner 'eki' means the build runs at
background priority and, under autonomy propose, the item is only proposed
(eki/selfwork.py, eki/queue.py). The housekeeping pass calls `tick`.
"""
from __future__ import annotations

import logging
import posixpath
import sqlite3
from typing import Any, Dict, List, Optional

from . import db, observe, selfwork

log = logging.getLogger("eki.faults")

DAY = 86400.0
RECENT = 7 * DAY            # a fault fixed this recently isn't opened again
SEEN = 2                    # occurrences within a day that make a fault worth an item
PROMPT_MAX = 2000
#: an item in one of these states is still the subject of work
OPEN = ("waiting", "building", "judging", "proposed", "locked", "queued", "resolving", "rechecking")
LANDED = ("landed", "live", "applied")


def key(entry: Dict[str, Any]) -> Optional[str]:
    """'eki/x.py:12 KeyError' for a fault in eki's own code; None for anything else."""
    data = entry.get("data") or {}
    if not data.get("ours") or not data.get("frame") or not data.get("exc"):
        return None
    return f"{data['frame']} {data['exc']}"


def test_file(path: str) -> str:
    """eki/a/b.py → tests/test_b.py"""
    return f"tests/test_{posixpath.basename(path)}"


def title(k: str) -> str:
    return f"fix fault {k}"


def tick(conn: sqlite3.Connection, now: Optional[float] = None) -> List[str]:
    now = db.now() if now is None else now
    seen: Dict[str, List[Dict[str, Any]]] = {}
    for e in observe.entries(conn, since=now - DAY, until=now + 1, kind="fault"):
        k = key(e)
        if k is not None:
            seen.setdefault(k, []).append(e)
    said: List[str] = []
    cap = int(selfwork.settings().get("fault_items_per_day", 3))
    for k, found in seen.items():
        if len(found) < SEEN or _known(conn, k, now):
            continue
        if _opened_today(conn, now) >= cap:
            break                                  # quietly: the pass runs often, the cap holds all day
        frame = found[-1]["data"]["frame"]
        path = frame.rsplit(":", 1)[0]
        try:
            gid = selfwork.submit(conn, _text(conn, k, found), plan=False, files=[path, test_file(path)],
                                  owner="eki", source_kind="fault")
        except Exception as e:                     # one fault's trouble doesn't stop the pass
            log.exception("faults: could not open %s", k)
            said.append(f"faults: could not open {k}: {str(e)[:160]}")
            continue
        said.append(f"goal {gid}: fix fault {k} (seen {len(found)} times today)")
    return said


def _opened_today(conn: sqlite3.Connection, now: float) -> int:
    return conn.execute("SELECT COUNT(*) FROM goals WHERE source='fault' AND created_at>=?",
                        (now - DAY,)).fetchone()[0]


def _known(conn: sqlite3.Connection, k: str, now: float) -> bool:
    """Is this fault already the subject of an open item, or of one landed lately?"""
    want = title(k)
    rows = conn.execute(
        "SELECT g.text, i.state, COALESCE(i.landed_at, i.updated_at) AS moved FROM goals g"
        " JOIN items i ON i.goal_id=g.id WHERE g.source='fault' AND g.text LIKE ?",
        (want.replace("%", "") + "%",)).fetchall()
    for r in rows:
        if (r["text"].splitlines() or [""])[0].strip() != want:
            continue
        if r["state"] in OPEN or (r["state"] in LANDED and r["moved"] >= now - RECENT):
            return True
    return False


def _text(conn: sqlite3.Connection, k: str, found: List[Dict[str, Any]]) -> str:
    latest = found[-1]
    tb = (latest["data"].get("traceback") or "").rstrip()
    where = latest["data"].get("where") or "?"
    lines = [
        title(k), "",
        f"eki's own code raised {k} {len(found)} times in the last 24 hours (caught in {where}).",
        "Make the fault stop: find why it happens and fix the cause, not the symptom. Add a test",
        "that reproduces it — it fails before your fix and passes after.", "",
        "The latest traceback:", "```", tb or "(none written down)", "```",
    ]
    request = _request(conn, found)
    if request:
        lines += ["", "The request of the run it happened in:", "```", request, "```"]
    return "\n".join(lines)


def _request(conn: sqlite3.Connection, found: List[Dict[str, Any]]) -> Optional[str]:
    for e in reversed(found):
        if not e.get("run_id"):
            continue
        r = conn.execute("SELECT prompt FROM runs WHERE id=?", (e["run_id"],)).fetchone()
        if r is not None and r["prompt"]:
            return observe.scrub(r["prompt"][:PROMPT_MAX])
    return None
