"""Pushes to the person's phone (and so their watch) through ntfy.

Two kinds only: a goal that ended, and something that waits on the person.
A hook calls queue() inside its own transaction; the row's key makes each
event at most one push, and the row living in the db means a restart
neither sends one twice nor loses one. The housekeeping pass calls tick(),
which claims the unsent rows and sends them on a thread of its own — the
engine never waits on the network.

Settings are the "notify" object in routing.json: server, topic (empty is
off) and events. The topic is the only secret: whoever knows it can read
the pushes, so it is never logged, stored in an error or printed whole.
"""
from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import urllib.request
from typing import Any, Dict, List, Optional

from . import db, paths

log = logging.getLogger("eki.notify")

KINDS = ("goal_done", "needs_you")
DEFAULTS: Dict[str, Any] = {"server": "https://ntfy.sh", "topic": "", "events": ["goal_done", "needs_you"]}

#: seconds one POST may take
TIMEOUT = 5
#: tries before a notice is given up (and logged, once)
MAX_TRIES = 3
#: a claimed row not sent by then (the drain died with it) is claimed again
RECLAIM = 60.0

_drain: List[Optional[threading.Thread]] = [None]


def settings() -> Dict[str, Any]:
    try:
        got = json.loads(paths.config("routing").read_text()).get("notify") or {}
    except (OSError, ValueError, AttributeError):
        got = {}
    return {**DEFAULTS, **(got if isinstance(got, dict) else {})}


def on(kind: str) -> bool:
    s = settings()
    return bool(s.get("topic")) and kind in (s.get("events") or [])


def masked(topic: str) -> str:
    return topic[:2] + "…" if topic else "off"


def _header(text: str) -> str:
    """HTTP headers are Latin-1; anything else goes RFC 2047 encoded, which ntfy decodes."""
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def headers(kind: str, title: str, click: Optional[str] = None) -> Dict[str, str]:
    tags = {"needs_you": "bell", "goal_done": "white_check_mark"}.get(kind, "test_tube")
    got = {"Title": _header(title), "Priority": "high" if kind == "needs_you" else "default", "Tags": tags}
    if click:
        got["Click"] = _header(click)
    return got


def url(server: str, topic: str) -> str:
    return f"{server.rstrip('/')}/{topic}"


def _scrub(text: str, topic: str) -> str:
    return text.replace(topic, masked(topic)) if topic else text


def _post(kind: str, title: str, body: str, click: Optional[str]) -> Optional[str]:
    """None when ntfy took it; otherwise why not, without the topic."""
    s = settings()
    topic = str(s.get("topic") or "")
    if not topic:
        return "notify is off"
    try:
        req = urllib.request.Request(url(str(s.get("server") or DEFAULTS["server"]), topic),
                                     data=body.encode("utf-8"), headers=headers(kind, title, click),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
        return None if 200 <= int(code) < 300 else f"HTTP {code}"
    except Exception as e:                               # noqa: BLE001 — a push never breaks anything
        return _scrub(f"{type(e).__name__}: {e}", topic)[:300]


def send(kind: str, title: str, body: str, click: Optional[str] = None) -> bool:
    """One POST, now. True when ntfy took it; never raises."""
    try:
        return _post(kind, title, body, click) is None
    except Exception:                                    # noqa: BLE001
        return False


def queue(conn: sqlite3.Connection, key: str, kind: str, title: str, body: str,
          click: Optional[str] = None) -> bool:
    """Write the notice down once per key (no transaction of its own). True when it is new."""
    try:
        if not on(kind):
            return False
        cur = conn.execute("INSERT OR IGNORE INTO notices (key, kind, title, body, click, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (key, kind, title, body, click, db.now()))
        return cur.rowcount == 1
    except Exception:                                    # noqa: BLE001
        return False


def _send_all(rows: List[Dict[str, Any]]) -> None:
    conn = db.connect()
    try:
        for r in rows:
            why = _post(r["kind"], r["title"], r["body"], r["click"])
            with db.tx(conn):
                if why is None:
                    conn.execute("UPDATE notices SET sent_at=?, sending_at=NULL, error=NULL WHERE key=?",
                                 (db.now(), r["key"]))
                else:
                    conn.execute("UPDATE notices SET tries=tries+1, sending_at=NULL, error=? WHERE key=?",
                                 (why, r["key"]))
            if why is not None and r["tries"] + 1 >= MAX_TRIES:
                log.warning("notice %s not sent after %d tries: %s", r["key"], MAX_TRIES, why)
    except Exception:                                    # noqa: BLE001 — a claimed row is taken again later
        log.warning("the notify drain stopped early; its rows are tried again in %ds", int(RECLAIM))
    finally:
        conn.close()


def tick(conn: sqlite3.Connection) -> List[str]:
    """Claim the unsent notices and send them on a thread of their own; returns at once."""
    if not settings().get("topic"):
        return []                                        # kept until notify is on again
    t = _drain[0]
    if t is not None and t.is_alive():
        return []
    now = db.now()
    with db.tx(conn):
        rows = [dict(r) for r in conn.execute(
            "SELECT key, kind, title, body, click, tries FROM notices WHERE sent_at IS NULL AND tries < ? "
            "AND (sending_at IS NULL OR sending_at < ?) ORDER BY created_at", (MAX_TRIES, now - RECLAIM))]
        for r in rows:
            conn.execute("UPDATE notices SET sending_at=? WHERE key=?", (now, r["key"]))
    if not rows:
        return []
    _drain[0] = threading.Thread(target=_send_all, args=(rows,), daemon=True, name="notify")
    _drain[0].start()
    return [f"notify: sending {len(rows)} notice{'s' if len(rows) != 1 else ''}"]


def wait(timeout: float = 10.0) -> None:
    """Until the drain thread is done (for tests and `eki notify test`)."""
    t = _drain[0]
    if t is not None:
        t.join(timeout)
