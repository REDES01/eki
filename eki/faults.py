"""Faults become items (docs/self-build.md, "Items").

A traceback in eki's own code that the journal (eki/observe.py) saw twice
within a day — same file:line, same exception — opens a goal of its own:
source 'fault', owner 'eki', one item "fix this" whose write-set is the file
in the top frame and its test file. Owner 'eki' means the build runs at
background priority and, under autonomy propose, the item is only proposed
(eki/selfwork.py, eki/queue.py). The housekeeping pass calls `tick`; the
picker (eki/selfpick.py) opens faults seen even once lately, through `pending`,
`room` and `open_goal`.

Opening a goal also asks the local model for a first draft of the item's spec
(a `brief` chore, eki/chores.py): likely cause, where to look, what the
reproducing test should do. The item waits for it up to `self.brief_wait`
minutes (eki/selfwork.py `held`); `brief_tick` puts a done draft in front of
the template's traceback and request, kept verbatim. Anything else — no local
model, a failed run, an answer without `BRIEF:`, an item already started —
leaves the template as it is.
"""
from __future__ import annotations

import logging
import posixpath
import sqlite3
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import builds, chores, db, integration, observe, selfwork, store

log = logging.getLogger("eki.faults")

DAY = 86400.0
RECENT = 7 * DAY            # a fault fixed this recently isn't opened again
SEEN = 2                    # occurrences within a day that make a fault worth an item
PROMPT_MAX = 2000
AROUND = 60                 # lines of the fault's file on each side of the frame, for the brief
TAIL = "The latest traceback:"
#: an item in one of these states is still the subject of work
OPEN = ("waiting", "building", "judging", "reviewing", "proposed", "locked", "queued", "resolving", "rechecking")
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
    said: List[str] = brief_tick(conn)
    for k, found in _seen(conn, now - DAY, now).items():
        if len(found) < SEEN or _known(conn, k, now):
            continue
        if not room(conn, now):
            break                                  # quietly: the pass runs often, the cap holds all day
        try:
            gid = open_goal(conn, k, found, f"fault {k}, seen {len(found)} times today", now=now)
        except Exception as e:                     # one fault's trouble doesn't stop the pass
            log.exception("faults: could not open %s", k)
            said.append(f"faults: could not open {k}: {str(e)[:160]}")
            continue
        said.append(f"goal {gid}: fix fault {k} (seen {len(found)} times today)")
    return said


def open_goal(conn: sqlite3.Connection, k: str, found: List[Dict[str, Any]], why: str,
              now: Optional[float] = None) -> str:
    """Open the goal for fault `k`: one item, write-set the top frame's file and its test."""
    path = found[-1]["data"]["frame"].rsplit(":", 1)[0]
    text = _text(conn, k, found, db.now() if now is None else now)
    gid = selfwork.submit(conn, text, plan=False, files=[path, test_file(path)], owner="eki",
                          source_kind="fault", why=why, pick_key=f"fault:{k}")
    try:
        with db.tx(conn):
            chores.start(conn, "brief", gid, brief_prompt(text, found[-1]), "background")
    except Exception:                              # the template stands without a brief
        log.exception("faults: could not ask for a brief of %s", k)
    return gid


# ---- the brief ------------------------------------------------------------------------------

BRIEF = """\
A piece of eki, a program on this Mac, keeps failing. Another agent will fix
it; write the first draft of its brief. Do not change anything.

What eki wrote down about the fault:

{template}

The code around the frame ({frame}):

```python
{code}
```

Answer with `BRIEF:` on a line of its own, then the brief in plain words:
the likely cause, where to look (files and functions), and what the test that
reproduces the fault should do. Nothing after the brief.
"""


def brief_prompt(template: str, latest: Dict[str, Any]) -> str:
    return BRIEF.format(template=template, frame=latest["data"]["frame"], code=_around(latest["data"]["frame"]))


def _around(frame: str) -> str:
    """The fault's file, ±AROUND lines of the frame, numbered; from the source or the integration repo."""
    path, _, line = frame.rpartition(":")
    try:
        at = int(line)
    except ValueError:
        return "(no line)"
    for root in (builds.source(), integration.repo()):
        try:
            lines = (Path(root) / path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        lo, hi = max(1, at - AROUND), min(len(lines), at + AROUND)
        return "\n".join(f"{n:5} {lines[n - 1]}" for n in range(lo, hi + 1))
    return "(the file could not be read)"


def draft(answer: str) -> Optional[str]:
    """What follows the last `BRIEF:` line, or None."""
    got = None
    lines = (answer or "").splitlines()
    for n, l in enumerate(lines):
        if l.strip().lstrip("*#").strip().upper().startswith("BRIEF:"):
            first = l.split(":", 1)[1].strip().strip("*").strip()
            got = "\n".join(([first] if first else []) + lines[n + 1:]).strip()
    return got or None


def brief_tick(conn: sqlite3.Connection) -> List[str]:
    """Move each finished brief chore on once: a done draft becomes the spec of the
    goal's item if it is still waiting and not started; anything else leaves the template."""
    said = []
    for c in chores.finished(conn, "brief"):
        text = draft(store.answer(conn, c["run_id"])) if c["run_state"] == "done" else None
        with db.tx(conn):
            if text is None:
                why = ("no BRIEF: in the answer" if c["run_state"] == "done"
                       else c["run_error"] or f"run {c['run_state'] or 'gone'}")
                if chores.close(conn, c["id"], "failed", why):
                    said.append(f"goal {c['subject']}: no brief ({why[:80]}), the template stands")
                continue
            if not chores.close(conn, c["id"], "done", text):
                continue
            it = conn.execute("SELECT * FROM items WHERE goal_id=? AND state='waiting' AND run_id IS NULL"
                              " ORDER BY created_at LIMIT 1", (c["subject"],)).fetchone()
            if it is None:
                said.append(f"goal {c['subject']}: brief came after the build started, unused")
                continue
            spec = it["spec"] or ""
            at = spec.find(TAIL)
            spec = text + ("\n\n" + spec[at:] if at >= 0 else "")
            selfwork._set(conn, it["id"], spec=spec)
            said.append(f"item {it['id']}: spec drafted by the local model")
    return said


def pending(conn: sqlite3.Connection, since: float,
            now: Optional[float] = None) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Every fault seen at least once in [since, now] that no open or lately landed
    item is about: most seen first, then the latest sighting first."""
    now = db.now() if now is None else now
    out = [(k, found) for k, found in _seen(conn, since, now).items() if not _known(conn, k, now)]
    out.sort(key=lambda kf: (-len(kf[1]), -kf[1][-1]["t"]))
    return out


def room(conn: sqlite3.Connection, now: Optional[float] = None) -> bool:
    """Are fewer fault goals open in the last 24 hours than the daily cap?"""
    now = db.now() if now is None else now
    return _opened_today(conn, now) < int(selfwork.settings().get("fault_items_per_day", 3))


def _seen(conn: sqlite3.Connection, since: float, now: float) -> Dict[str, List[Dict[str, Any]]]:
    seen: Dict[str, List[Dict[str, Any]]] = {}
    for e in observe.entries(conn, since=since, until=now + 1, kind="fault"):
        k = key(e)
        if k is not None:
            seen.setdefault(k, []).append(e)
    return seen


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


def _text(conn: sqlite3.Connection, k: str, found: List[Dict[str, Any]], now: float) -> str:
    latest = found[-1]
    span = now - found[0]["t"]
    window = "the last 24 hours" if span <= DAY else f"the last {math.ceil(span / DAY)} days"
    tb = (latest["data"].get("traceback") or "").rstrip()
    where = latest["data"].get("where") or "?"
    lines = [
        title(k), "",
        f"eki's own code raised {k} {len(found)} times in {window} (caught in {where}).",
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
