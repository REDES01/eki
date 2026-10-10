"""Scheduled prompts: a prompt sent as a new chat on a clock.

A `schedules` row holds the prompt, its project, its cadence (JSON: hourly
at a minute, daily, weekdays or weekly at a time), the IANA zone fixed when
it was added, and an optional target (targets.fmt as JSON; NULL is Auto).
The engine's housekeeping calls tick() every pass: each schedule that is on
and due fires once — a new thread and a run, all in one transaction — and
its next_at moves to the first firing after now, so a Mac asleep through
several firings runs one on wake, never a stack. A firing whose last chat
is still running is skipped and says why. settle() queues one goal-done
notice per fired chat when it ends. Nothing lives in memory: a restart
mid-tick either committed the firing with its next_at or did neither.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db, modellist, notify, projectchats, providers, store, targets

EVERY = ("hourly", "daily", "weekdays", "weekly")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: how far back a fired chat's end still earns a notice
WINDOW = 24 * 3600.0


# ---- cadence and time -------------------------------------------------------------------------

def _at(text: Any) -> Tuple[int, int]:
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        raise ValueError(f"time {text!r} is not HH:MM") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time {text!r} is not HH:MM (00:00 to 23:59)")
    return h, m


def check(cadence: Any) -> Dict[str, Any]:
    """The cadence as a clean dict; ValueError with a plain message when it is wrong."""
    if isinstance(cadence, str):
        try:
            cadence = json.loads(cadence)
        except ValueError:
            raise ValueError(f"cadence {cadence!r} is not JSON") from None
    if not isinstance(cadence, dict):
        raise ValueError("a cadence is an object with 'every'")
    every = cadence.get("every")
    if every not in EVERY:
        raise ValueError(f"every {every!r} is not one of {', '.join(EVERY)}")
    if every == "hourly":
        m = cadence.get("minute", 0)
        try:
            m = int(m)
        except (TypeError, ValueError):
            raise ValueError(f"minute {m!r} is not a number") from None
        if not 0 <= m <= 59:
            raise ValueError(f"minute {m} is not 0 to 59")
        return {"every": "hourly", "minute": m}
    h, m = _at(cadence.get("at"))
    out: Dict[str, Any] = {"every": every, "at": f"{h:02d}:{m:02d}"}
    if every == "weekly":
        day = str(cadence.get("day") or "").strip().lower()[:3]
        if day not in DAYS:
            raise ValueError(f"day {cadence.get('day')!r} is not one of {', '.join(DAYS)}")
        out["day"] = day
    return out


def cadence_text(cadence: Any) -> str:
    """'Hourly at :15', 'Daily at 09:00', 'Weekdays at 09:00', 'Weekly on Mon at 09:00'."""
    c = check(cadence)
    if c["every"] == "hourly":
        return f"Hourly at :{c['minute']:02d}"
    if c["every"] == "weekly":
        return f"Weekly on {c['day'].capitalize()} at {c['at']}"
    return f"{c['every'].capitalize()} at {c['at']}"


def next_fire(cadence: Any, after: float, tz: str) -> float:
    """The first firing strictly after `after`, in zone `tz`. Pure."""
    c = check(cadence)
    zone = ZoneInfo(tz)
    here = datetime.fromtimestamp(after, zone).replace(tzinfo=None)   # wall clock
    if c["every"] == "hourly":
        base = here.replace(minute=c["minute"], second=0, microsecond=0)
        walls = [base + timedelta(hours=k) for k in range(-1, 4)]
    else:
        h, m = _at(c["at"])
        walls = []
        for k in range(0, 9):
            day = here.date() + timedelta(days=k)
            wd = day.weekday()
            if c["every"] == "weekdays" and wd > 4:
                continue
            if c["every"] == "weekly" and DAYS[wd] != c["day"]:
                continue
            walls.append(datetime.combine(day, dtime(h, m)))
    for wall in walls:
        t = wall.replace(tzinfo=zone).timestamp()
        if t > after:
            return t
    raise ValueError(f"no firing found for {c!r}")      # not reachable for a checked cadence


def local_tz() -> str:
    """EKI_TZ, else the zone /etc/localtime links to, else UTC."""
    for name in (os.environ.get("EKI_TZ", "").strip(), _linked()):
        if name:
            try:
                ZoneInfo(name)
                return name
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return "UTC"


def _linked() -> str:
    real = os.path.realpath("/etc/localtime")
    _, mark, name = real.partition("zoneinfo/")
    return name if mark else ""


# ---- the rows ---------------------------------------------------------------------------------

def _target(target: Any) -> Optional[str]:
    if target is None or target == "" or target == "auto":
        return None
    bad = targets.problems(target, set(providers.config()))
    if bad:
        raise ValueError("; ".join(bad))
    return json.dumps(targets.fmt(targets.parse(target)))


def add(conn: sqlite3.Connection, text: str, cadence: Any, project: Optional[str] = None,
        target: Any = None) -> str:
    """A new schedule, on; ValueError (or KeyError for an unknown project) when it can't be."""
    text = (text or "").strip()
    if not text:
        raise ValueError("a scheduled prompt needs words")
    c = check(cadence)
    pid = None
    if project:
        pid = projectchats.find(conn, project)
        if not projectchats.folder_of(conn, pid):
            raise ValueError(f"project {project} has no folder yet")
    tgt = _target(target)
    tz = local_tz()
    sid = store.new_id()
    t = db.now()
    with db.tx(conn):
        conn.execute("INSERT INTO schedules(id, text, project, cadence, tz, target, state, next_at, created_at)"
                     " VALUES (?,?,?,?,?,?,'on',?,?)",
                     (sid, text, pid, json.dumps(c), tz, tgt, next_fire(c, t, tz), t))
    return sid


def _public(r: sqlite3.Row) -> Dict[str, Any]:
    out = dict(r)
    out["cadence"] = json.loads(r["cadence"])
    out["cadence_text"] = cadence_text(out["cadence"])
    out["target"] = json.loads(r["target"]) if r["target"] else None
    return out


def listing(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Every schedule not dropped, oldest first."""
    rows = conn.execute("SELECT * FROM schedules WHERE state!='dropped' ORDER BY created_at").fetchall()
    return [_public(r) for r in rows]


def find(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    """The schedule whose id is or starts with `sid`; KeyError if none or several."""
    sid = (sid or "").strip()
    rows = conn.execute("SELECT * FROM schedules WHERE id LIKE ? ORDER BY created_at",
                        (sid + "%",)).fetchall() if sid else []
    exact = [r for r in rows if r["id"] == sid]
    if exact:
        return exact[0]
    if len(rows) != 1:
        raise KeyError(f"no schedule {sid}" if not rows else f"{sid} is more than one schedule")
    return rows[0]


def _set(conn: sqlite3.Connection, sid: str, **fields: Any) -> str:
    s = find(conn, sid)
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx(conn):
        conn.execute(f"UPDATE schedules SET {cols} WHERE id=?", (*fields.values(), s["id"]))
    return s["id"]


def pause(conn: sqlite3.Connection, sid: str) -> str:
    return _set(conn, sid, state="paused")


def resume(conn: sqlite3.Connection, sid: str) -> str:
    """On again; the next firing counts from now, so nothing missed while paused runs."""
    s = find(conn, sid)
    return _set(conn, s["id"], state="on", why=None, next_at=next_fire(s["cadence"], db.now(), s["tz"]))


def drop(conn: sqlite3.Connection, sid: str) -> str:
    return _set(conn, sid, state="dropped")


# ---- firing -----------------------------------------------------------------------------------

def _busy(conn: sqlite3.Connection, tid: Optional[str]) -> bool:
    if not tid:
        return False
    marks = ",".join("?" * len(store.ACTIVE))
    return conn.execute(f"SELECT 1 FROM runs WHERE thread_id=? AND state IN ({marks}) LIMIT 1",
                        (tid, *store.ACTIVE)).fetchone() is not None


def _fire(conn: sqlite3.Connection, s: sqlite3.Row, t: float, next_at: Optional[float]) -> str:
    """One firing, inside the caller's transaction: the thread, its run, the row's marks."""
    folder = projectchats.folder_of(conn, s["project"]) if s["project"] else None
    provider = model = effort = None
    if s["target"]:
        tgt = targets.parse(json.loads(s["target"]))
        provider = tgt.provider          # pinned, as asking.submit does for `to`
        c = targets.choose(tgt, providers.config().get(provider) or {}, modellist.listed(conn, provider))
        model, effort = c.model, c.effort
    tid = store.create_thread(conn, s["text"].splitlines()[0], folder)
    conn.execute("UPDATE threads SET project=?, schedule=? WHERE id=?", (s["project"], s["id"], tid))
    store.create_run(conn, tid, s["text"], provider=provider, model=model, effort=effort, priority="now")
    conn.execute("UPDATE schedules SET last_at=?, last_thread=?, next_at=?, why=NULL WHERE id=?",
                 (t, tid, next_at, s["id"]))
    return tid


def run_now(conn: sqlite3.Connection, sid: str) -> str:
    """Fire it now, whatever its state; next_at stays. The new thread's id."""
    s = find(conn, sid)
    with db.tx(conn):
        return _fire(conn, s, db.now(), s["next_at"])


def tick(conn: sqlite3.Connection, now: Optional[float] = None) -> List[str]:
    """Fire each schedule that is on and due, once; one line per firing or skip."""
    t = db.now() if now is None else now
    said: List[str] = []
    due = conn.execute("SELECT id FROM schedules WHERE state='on' AND next_at IS NOT NULL AND next_at<=?",
                       (t,)).fetchall()
    for d in due:
        with db.tx(conn):
            s = conn.execute("SELECT * FROM schedules WHERE id=? AND state='on' AND next_at<=?",
                             (d["id"], t)).fetchone()
            if s is None:
                continue                                 # fired or paused since the read
            nxt = next_fire(s["cadence"], t, s["tz"])
            if _busy(conn, s["last_thread"]):
                at = datetime.fromtimestamp(s["next_at"], ZoneInfo(s["tz"])).strftime("%H:%M")
                conn.execute("UPDATE schedules SET next_at=?, why=? WHERE id=?",
                             (nxt, f"skipped {at}: the last one is still running", s["id"]))
                said.append(f"schedule {s['id']}: skipped, the last one is still running")
                continue
            tid = _fire(conn, s, t, nxt)
            said.append(f"schedule {s['id']}: fired, thread {tid}")
    return said


def settle(conn: sqlite3.Connection) -> List[str]:
    """One notice per fired chat that ended in the last day (notify on for goal_done)."""
    if not notify.on("goal_done"):
        return []
    marks = ",".join("?" * len(store.ACTIVE))
    rows = conn.execute(
        "SELECT t.id, s.text FROM threads t JOIN schedules s ON s.id=t.schedule "
        "WHERE t.deleted_at IS NULL "
        f"AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.thread_id=t.id AND r.state IN ({marks})) "
        "AND (SELECT MAX(r.ended_at) FROM runs r WHERE r.thread_id=t.id) >= ?",
        (*store.ACTIVE, db.now() - WINDOW)).fetchall()
    said: List[str] = []
    for r in rows:
        key = f"schedule:{r['id']}:done"
        if conn.execute("SELECT 1 FROM notices WHERE key=?", (key,)).fetchone():
            continue
        last = conn.execute("SELECT state FROM runs WHERE thread_id=? ORDER BY seq DESC LIMIT 1",
                            (r["id"],)).fetchone()
        title = "eki · scheduled: failed" if last and last["state"] == "failed" else "eki · scheduled: done"
        line = next((ln.strip() for ln in r["text"].splitlines() if ln.strip()), "")[:120]
        with db.tx(conn):
            if notify.queue(conn, key, "goal_done", title, line):
                said.append(f"notice queued: scheduled chat {r['id']}")
    return said
