"""The daily digest: two Markdown pages a day of what eki did to itself
(docs/self-build.md, "The journal, the score and the digest").

The short page, EKI_HOME/self/digests/YYYY-MM-DD.md, is patch notes: what
landed on eki itself, one line per area (patchnotes.rules_page), then how
many things wait for you and the score's verdict. The long page beside it,
YYYY-MM-DD.long.md, lists every item that changed state since the last page,
one line each, none left out, with the score table and worse builds.

Both are built from the items table, the journal and build_scores — never by
a model (digestprose may later swap in checked prose). '.last' beside the
pages says when the last one ended, so the next starts there. Holds no state
beyond those files: a restart changes nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db, observe, patchnotes, paths, projects, score, selfwork

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


def long_of(page: Path) -> Path:
    """The long page beside a short one: YYYY-MM-DD.md -> YYYY-MM-DD.long.md."""
    return page.with_name(f"{page.stem}.long.md")


def last() -> Optional[float]:
    """When the last page ended, or None before the first."""
    try:
        return float((folder() / ".last").read_text().strip())
    except (OSError, ValueError):
        return None


def latest() -> Optional[Path]:
    """The newest short page; the glob never matches a .long.md."""
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


def window_start(now: float) -> float:
    """Where today's page begins: the end of the page before it — kept beside the page,
    so writing today's page again (`eki self digest now`) covers the same span rather
    than the minutes since the last write."""
    keep = folder() / f".{_date(now)}.since"
    try:
        since = float(keep.read_text().strip())
        if since < now:
            return since
    except (OSError, ValueError):
        pass
    since = last()
    if since is None or since >= now:
        since = now - DAY
    _atomic(keep, repr(since))
    return since


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write(conn: sqlite3.Connection, now: Optional[float] = None) -> Path:
    """Write today's pages (again, if they are there) for the time since the last one:
    the short page first, then the long one, then '.last'."""
    now = db.now() if now is None else now
    since = window_start(now)
    path = page_for(now)
    _atomic(path, patchnotes.rules_page(changes(conn, since, now), now, waits(conn),
                                        verdict(conn, since, now)))
    long = render(conn, since, now).replace(f"# eki digest — {_date(now)}",
                                            f"# eki digest — {_date(now)} (long)", 1)
    _atomic(long_of(path), long)
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


def _merge_hint(it: sqlite3.Row, project: sqlite3.Row) -> str:
    return f"`git -C {project['path']} merge {it['branch'] or 'eki/' + it['id']}`"


def happened(it: sqlite3.Row, autonomy: str, project: Optional[sqlite3.Row] = None) -> Tuple[str, str]:
    """(group, what happened) for one item; `project` when its goal works on a person's repo."""
    state, iid = it["state"], it["id"]
    if project is not None and state in ("locked", "proposed", "applied"):
        if state == "applied":
            return GROUPS[0], f"merged into {project['branch']}"
        return GROUPS[2], f"proposed on {it['branch'] or 'eki/' + iid}; {_merge_hint(it, project)}"
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
        if (it["review"] or "").startswith("no:"):
            objection = _first_line(it["review"][3:]) or "no reason given"
            return GROUPS[2], f"a second reader objected twice: {objection}; `eki self apply {iid}` if you agree"
        if autonomy == "propose":
            return GROUPS[2], f"proposed; `eki self apply {iid}`"
        return GROUPS[2], "proposed"
    return GROUPS[3], state


def changed(conn: sqlite3.Connection, since: float, until: float) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE updated_at>=? AND updated_at<? ORDER BY updated_at, id",
                        (since, until)).fetchall()


def project_of(conn: sqlite3.Connection, it: sqlite3.Row) -> Optional[sqlite3.Row]:
    """The project an item's goal works on, or None for eki itself."""
    g = conn.execute("SELECT project FROM goals WHERE id=?", (it["goal_id"],)).fetchone()
    return projects.get(conn, g["project"]) if g is not None and g["project"] else None


def _paths(it: sqlite3.Row) -> List[str]:
    for col in ("touched", "files"):
        try:
            got = json.loads(it[col] or "[]")
        except (ValueError, TypeError):
            got = []
        if isinstance(got, list) and got:
            return [str(p) for p in got]
    return []


def changes(conn: sqlite3.Connection, since: float, until: float) -> List[patchnotes.Change]:
    """What landed on eki itself in [since, until), for the short page."""
    out = []
    for it in changed(conn, since, until):
        if it["state"] in LANDED and project_of(conn, it) is None:
            got = _paths(it)
            out.append(patchnotes.Change(it["id"], it["title"] or "", patchnotes.area_of(got),
                                         patchnotes.kind_of(got)))
    return out


def waits(conn: sqlite3.Connection) -> int:
    """What waits for you now, whatever the window: locked and proposed items, open asks."""
    n = conn.execute(f"SELECT COUNT(*) FROM items WHERE state IN ({','.join('?' * len(WAITS))})",
                     WAITS).fetchone()[0]
    return n + len(asking(conn))


def verdict(conn: sqlite3.Connection, since: float, until: float) -> str:
    """The short page's score, in words."""
    worse = score.worse(conn, since)
    if worse:
        return f"worse: build {str(worse[-1]['build'])[:7]} judged worse, see --long"
    before = score.compute(conn, until - 2 * DAY, until - DAY)
    after = score.compute(conn, until - DAY, until)
    said = score.verdict(before, after)
    if said == "better":
        return "better than yesterday"
    if said != "worse":
        return "same as yesterday"
    if score._rose(before.get("fault_rate"), after.get("fault_rate")):
        return "worse: more faults"
    if score._rose(before.get("correction_rate"), after.get("correction_rate")):
        return "worse: more corrections"
    return "worse: less done locally"


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


def picked(conn: sqlite3.Connection, since: float, until: float) -> List[str]:
    """Goals eki picked for itself in the window, newest first, each with its why."""
    return [f"- {g['id']} {_first_line(g['text'])} — {_first_line(g['why']) or 'no reason written down'}"
            f" ({g['state']})"
            for g in conn.execute("SELECT id, text, why, state FROM goals WHERE pick_key IS NOT NULL"
                                  " AND created_at>=? AND created_at<? ORDER BY created_at DESC, id",
                                  (since, until))]


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
        project = project_of(conn, it)
        group, what = happened(it, autonomy, project)
        where = f" ({project['name']})" if project is not None else ""
        groups[group].append(f"- {it['id']} {it['title']}{where} — {what}")
    groups["Waits for you"] += asking(conn)

    out = [f"# eki digest — {_date(until)}", "",
           f"From {_clock(since)} to {_clock(until)}: {len(items)} item(s) changed.", ""]
    for g in GROUPS:
        if g == "Still moving" and not groups[g]:
            continue
        out += [f"## {g}", ""]
        out += groups[g] or ["Nothing."]
        out.append("")

    out += ["## Picked by eki", ""]
    out += picked(conn, since, until) or ["Nothing."]
    out.append("")

    out += ["## Score", ""]
    today = score.compute(conn, until - DAY, until)
    out += _score_table(today, score.compute(conn, until - 2 * DAY, until - DAY))
    left = today.get("left_out") or {}
    out += ["", f"not counted: {left.get('self', 0)} self-work, {left.get('picture', 0)} picture runs"]
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
