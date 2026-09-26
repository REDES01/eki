"""The daily digest: one Markdown page a day of what eki did to itself
(docs/self-build.md, "The journal, the score and the digest").

Built from the items table, the journal and build_scores — never by a model.
Every item that changed state since the last page is on it, one line each,
none left out. The page for a day is EKI_HOME/self/digests/YYYY-MM-DD.md;
'.last' beside the pages says when the last one ended, so the next starts
there. Holds no state beyond those files: a restart changes nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db, observe, paths, score, selfwork

DAY = 86400.0
DEFAULT_AT = "09:00"

LANDED = ("landed", "live", "applied")
WRONG = ("unfit", "rolled back", "left")
WAITS = ("locked", "proposed")
GROUPS = ("Landed and live", "Went wrong", "Waits for you", "Still moving")

#: the score's rows, in the order the table shows them
ROWS = [("runs", "runs"), ("local_share", "local share"), ("fault_rate", "faults / 100 runs"),
        ("correction_rate", "corrections / 100 runs"), ("handoff_rate", "handoffs / 100 runs"),
        ("median_first_text", "median s to first text")]


def folder() -> Path:
    p = paths.home() / "self" / "digests"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _date(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def page_for(now: float) -> Path:
    return folder() / f"{_date(now)}.md"


def last() -> Optional[float]:
    """When the last page ended, or None before the first."""
    try:
        return float((folder() / ".last").read_text().strip())
    except (OSError, ValueError):
        return None


def latest() -> Optional[Path]:
    pages = sorted(folder().glob("????-??-??.md"))
    return pages[-1] if pages else None


def _at_minutes() -> int:
    text = str(selfwork.settings().get("digest_at") or DEFAULT_AT)
    try:
        h, m = text.strip().split(":", 1)
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except ValueError:
        pass
    h, m = DEFAULT_AT.split(":")
    return int(h) * 60 + int(m)


def due(now: Optional[float] = None) -> bool:
    """Past `self.digest_at` local time, and no page for today yet."""
    now = db.now() if now is None else now
    lt = time.localtime(now)
    return lt.tm_hour * 60 + lt.tm_min >= _at_minutes() and not page_for(now).exists()


def tick(conn: sqlite3.Connection, now: Optional[float] = None) -> Optional[Path]:
    return write(conn, now) if due(now) else None


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write(conn: sqlite3.Connection, now: Optional[float] = None) -> Path:
    """Write today's page (again, if it is there) for the time since the last one."""
    now = db.now() if now is None else now
    since = last()
    if since is None or since >= now:
        since = now - DAY
    path = page_for(now)
    _atomic(path, render(conn, since, now))
    _atomic(folder() / ".last", repr(now))
    return path


# ---- the page ---------------------------------------------------------------------------

def _first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _why(it: sqlite3.Row) -> str:
    return _first_line(it["error"]) or _first_line(it["verdict"]) or "no reason written down"


def _locked(it: sqlite3.Row) -> List[str]:
    try:
        got = json.loads(it["locked"] or "[]")
    except (ValueError, TypeError):
        return []
    return [str(f) for f in got] if isinstance(got, list) else []


def happened(it: sqlite3.Row, autonomy: str) -> Tuple[str, str]:
    """(group, what happened) for one item."""
    state, iid = it["state"], it["id"]
    if state in LANDED:
        build = it["build"]
        if state == "live":
            return GROUPS[0], f"live in build {build}" if build else "live"
        if state == "applied":
            return GROUPS[0], "applied to your checkout"
        return GROUPS[0], f"landed; carried by build {build}" if build else "landed in integration main; no build yet"
    if state in WRONG:
        why = _why(it)
        if state == "unfit" and (it["error"] or "").startswith("the train's check failed"):
            return GROUPS[1], f"reverted by the train — {_first_line(it['verdict']) or why}"
        if state == "left":
            return GROUPS[1], f"left — waits for you: {why}"
        return GROUPS[1], f"{state} — {why}"
    if state == "locked":
        files = ", ".join(_locked(it)) or "a locked file"
        return GROUPS[2], f"locked: touches {files}; `eki self apply {iid}`"
    if state == "proposed":
        if autonomy == "propose":
            return GROUPS[2], f"proposed; `eki self apply {iid}`"
        return GROUPS[2], "proposed"
    return GROUPS[3], state


def changed(conn: sqlite3.Connection, since: float, until: float) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE updated_at>=? AND updated_at<? ORDER BY updated_at, id",
                        (since, until)).fetchall()


def _question(payload: Optional[str]) -> str:
    """The first question an ask puts to you, in one line."""
    try:
        got = json.loads(payload or "{}")
    except (ValueError, TypeError):
        return ""
    qs = got.get("questions") if isinstance(got, dict) else None
    if isinstance(qs, list):
        for q in qs:
            if isinstance(q, dict) and _first_line(q.get("question")):
                return _first_line(q.get("question"))
    return _first_line(got.get("question")) if isinstance(got, dict) and isinstance(got.get("question"), str) else ""


def asking(conn: sqlite3.Connection) -> List[str]:
    """Goals still drafting whose draft run has a question open for you — listed whatever the window."""
    out = []
    for g in conn.execute("SELECT g.id, g.wish, g.text, a.id AS ask, a.payload FROM goals g"
                          " JOIN asks a ON a.run_id=g.draft_run AND a.state='open'"
                          " WHERE g.state='drafting' ORDER BY g.created_at, g.id, a.created_at"):
        if out and out[-1][0] == g["id"]:
            continue                         # one line a goal: its first open question
        what = _first_line(g["wish"]) or _first_line(g["text"])
        q = _question(g["payload"]) or "a question"
        out.append((g["id"], f"- goal {g['id']} {what} — asked you: {q} (eki answer {g['ask']})"))
    return [line for _, line in out]


def _clock(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _fmt(key: str, v: Any) -> str:
    if v is None:
        return "—"
    if key == "local_share":
        return f"{100 * v:.0f}%"
    if key == "runs":
        return str(v)
    return f"{v:.1f}" if isinstance(v, (int, float)) else str(v)


def _score_table(today: Dict[str, Any], before: Dict[str, Any]) -> List[str]:
    out = ["| | last 24 h | the 24 h before |", "|---|---|---|"]
    for key, label in ROWS:
        out.append(f"| {label} | {_fmt(key, today.get(key))} | {_fmt(key, before.get(key))} |")
    return out


def render(conn: sqlite3.Connection, since: float, until: float) -> str:
    autonomy = str(selfwork.settings().get("autonomy") or "propose")
    groups: Dict[str, List[str]] = {g: [] for g in GROUPS}
    items = changed(conn, since, until)
    for it in items:
        group, what = happened(it, autonomy)
        groups[group].append(f"- {it['id']} {it['title']} — {what}")
    groups["Waits for you"] += asking(conn)

    out = [f"# eki digest — {_date(until)}", "",
           f"From {_clock(since)} to {_clock(until)}: {len(items)} item(s) changed.", ""]
    for g in GROUPS:
        if g == "Still moving" and not groups[g]:
            continue
        out += [f"## {g}", ""]
        out += groups[g] or ["Nothing."]
        out.append("")

    out += ["## Score", ""]
    out += _score_table(score.compute(conn, until - DAY, until), score.compute(conn, until - 2 * DAY, until - DAY))
    faults = len(observe.entries(conn, since=since, until=until, kind="fault"))
    corrections = len(observe.entries(conn, since=since, until=until, kind="correction"))
    out += ["", f"In the journal since the last page: {faults} fault(s), {corrections} correction(s).", ""]

    out += ["## Worse builds", ""]
    worse = score.worse(conn, since=since)
    for b in worse:
        out.append(f"- build {b['build']} was judged worse than the one before it — `eki self undo {b['build']}`")
    if not worse:
        out.append("None.")
    out.append("")
    return "\n".join(out)
