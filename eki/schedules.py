# SPDX-License-Identifier: Apache-2.0
"""Things eki does on a timetable.

A schedule is a request eki makes on your behalf at set times: a prompt,
optionally a folder and a provider, and when — every so many minutes, or
daily at a time on chosen days. Each firing is an ordinary run in a fresh
thread named after the schedule and the time, routed like anything you
type (or pinned to the provider you chose), so it shows up in the chat
list, keeps its reason line, and can be resumed or retried. The engine
runs them whether or not the app is open; a time that passed while the
Mac slept fires once when it wakes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    cwd         TEXT NOT NULL DEFAULT '',
    backend     TEXT NOT NULL DEFAULT '',
    spec        TEXT NOT NULL,              -- {"kind": "interval", "minutes": 60} | {"kind": "daily", "at": "09:00", "days": [0..6]}
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    last_run_at INTEGER,
    last_conversation TEXT NOT NULL DEFAULT '',
    last_run    TEXT NOT NULL DEFAULT '',
    next_run_at INTEGER
);
"""
#: a firing this late after its time is skipped rather than run stale
CATCH_UP_SECONDS = 6 * 3600


@dataclass
class Schedule:
    id: str
    name: str
    prompt: str
    spec: Dict[str, Any]
    cwd: str = ""
    backend: str = ""
    enabled: bool = True
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_run_at: Optional[int] = None
    last_conversation: str = ""
    last_run: str = ""
    next_run_at: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        out = asdict(self)
        out["when"] = describe(self.spec)
        return out


def describe(spec: Dict[str, Any]) -> str:
    kind = spec.get("kind")
    if kind == "interval":
        minutes = int(spec.get("minutes", 60))
        if minutes % 1440 == 0:
            return f"every {minutes // 1440} day{'s' if minutes > 1440 else ''}"
        if minutes % 60 == 0:
            return f"every {minutes // 60} hour{'s' if minutes > 60 else ''}"
        return f"every {minutes} min"
    if kind == "daily":
        days = sorted(set(int(d) for d in spec.get("days") or range(7)))
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if days == list(range(7)):
            which = "every day"
        elif days == list(range(5)):
            which = "weekdays"
        elif days == [5, 6]:
            which = "weekends"
        else:
            which = ", ".join(names[d] for d in days)
        return f"{which} at {spec.get('at', '09:00')}"
    return "never"


def next_time(spec: Dict[str, Any], after: float, last: Optional[float] = None) -> Optional[float]:
    """The next firing strictly after `after` (unix seconds), local time."""
    kind = spec.get("kind")
    if kind == "interval":
        minutes = max(1, int(spec.get("minutes", 60)))
        base = last if last is not None else after
        nxt = base + minutes * 60
        while nxt <= after:
            nxt += minutes * 60
        return nxt
    if kind == "daily":
        hh, _, mm = str(spec.get("at", "09:00")).partition(":")
        try:
            hour, minute = int(hh), int(mm or 0)
        except ValueError:
            hour, minute = 9, 0
        days = set(int(d) for d in spec.get("days") or range(7))
        now = datetime.fromtimestamp(after)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(8):
            if candidate.timestamp() > after and candidate.weekday() in days:
                return candidate.timestamp()
            candidate += timedelta(days=1)
        return None
    return None


class Schedules:
    def __init__(self, path: Path | str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- reading -----------------------------------------------------

    def all(self) -> List[Schedule]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM schedules ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def get(self, sid: str) -> Optional[Schedule]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
        return self._row(row) if row else None

    def due(self, now: Optional[float] = None) -> List[Schedule]:
        """Enabled schedules whose time has come — and not so long ago that
        running them now would be running them stale."""
        now = now or time.time()
        out = []
        for s in self.all():
            if not s.enabled or s.next_run_at is None:
                continue
            if s.next_run_at <= now:
                if now - s.next_run_at <= CATCH_UP_SECONDS:
                    out.append(s)
                else:
                    self.advance(s.id, ran=False, now=now)       # too late: skip to the next
        return out

    # ---- writing -----------------------------------------------------

    def create(self, name: str, prompt: str, spec: Dict[str, Any], cwd: str = "",
               backend: str = "", enabled: bool = True) -> Schedule:
        s = Schedule(id=uuid.uuid4().hex[:12], name=name.strip() or "Scheduled task",
                     prompt=prompt, spec=spec, cwd=cwd, backend=backend, enabled=enabled)
        s.next_run_at = int(next_time(spec, time.time()) or 0) or None
        self._put(s)
        return s

    def update(self, sid: str, **fields: Any) -> Optional[Schedule]:
        s = self.get(sid)
        if s is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(s, k):
                setattr(s, k, v)
        if fields.get("spec") is not None or "enabled" in fields:
            nxt = next_time(s.spec, time.time()) if s.enabled else None
            s.next_run_at = int(nxt) if nxt else None
        self._put(s)
        return s

    def delete(self, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
            self._conn.commit()
        return cur.rowcount > 0

    def advance(self, sid: str, ran: bool, now: Optional[float] = None,
                conversation: str = "", run: str = "") -> Optional[Schedule]:
        """After a firing (or a skipped one): the next time, and what ran."""
        s = self.get(sid)
        if s is None:
            return None
        now = now or time.time()
        if ran:
            s.last_run_at = int(now)
            s.last_conversation = conversation
            s.last_run = run
        s.next_run_at = int(next_time(s.spec, now, last=now if s.spec.get("kind") == "interval" else None) or 0) or None
        self._put(s)
        return s

    def _put(self, s: Schedule) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO schedules (id, name, prompt, cwd, backend, spec, enabled, created_at,"
                " last_run_at, last_conversation, last_run, next_run_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, prompt=excluded.prompt,"
                " cwd=excluded.cwd, backend=excluded.backend, spec=excluded.spec,"
                " enabled=excluded.enabled, last_run_at=excluded.last_run_at,"
                " last_conversation=excluded.last_conversation, last_run=excluded.last_run,"
                " next_run_at=excluded.next_run_at",
                (s.id, s.name, s.prompt, s.cwd, s.backend, json.dumps(s.spec), int(s.enabled),
                 s.created_at, s.last_run_at, s.last_conversation, s.last_run, s.next_run_at))
            self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> Schedule:
        return Schedule(id=row["id"], name=row["name"], prompt=row["prompt"],
                        spec=json.loads(row["spec"] or "{}"), cwd=row["cwd"], backend=row["backend"],
                        enabled=bool(row["enabled"]), created_at=row["created_at"],
                        last_run_at=row["last_run_at"], last_conversation=row["last_conversation"],
                        last_run=row["last_run"], next_run_at=row["next_run_at"])
