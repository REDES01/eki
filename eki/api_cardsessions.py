"""An agent card's sessions, files served only from inside the session folder: routes for apiroutes.

A session is one folder journal.keep() wrote under sessions_dir()/<name>/<session>/. The list
reads each one's review line and taint; the detail adds the notes, the emotion timeline and the
screenshots; a file is served only when its resolved path (symlinks resolved) stays inside that
session's folder. Anything else is a KeyError, so the window sees a 404.
"""
from __future__ import annotations

import base64
import calendar
import json
import mimetypes
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import api_cards, components

MAX_IMAGE = 5 * 1024 * 1024
_STAMP = re.compile(r"^(\d{8}-\d{6})")
_PLAIN = re.compile(r"^[\w][\w.-]*$")


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _when(d: Path) -> float:
    m = _STAMP.match(d.name)
    if m:
        try:
            return float(calendar.timegm(time.strptime(m.group(1), "%Y%m%d-%H%M%S")))
        except ValueError:
            pass
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def _taint(d: Path) -> str:
    try:
        got = json.loads(_read(d / "session.json") or "{}")
    except ValueError:
        return ""
    t = got.get("tainted") if isinstance(got, dict) else ""
    return t if isinstance(t, str) else ("tainted" if t else "")


def _agent_root(conn: sqlite3.Connection, pid: str, name: str) -> Path:
    """sessions_dir()/<name> for an agent card; KeyError for an unknown card or another kind."""
    card = api_cards.card_of(conn, pid, name)
    if card["kind"] != "agent":
        raise KeyError(f"{name} is not an agent")
    return components.sessions_dir() / card["name"]


def _session(conn: sqlite3.Connection, pid: str, name: str, sid: str) -> Path:
    """The session's folder, resolved; KeyError unless sid is a plain folder name of this card."""
    if not _PLAIN.match(sid) or ".." in sid:
        raise KeyError(f"no session {sid}")
    root = _agent_root(conn, pid, name).resolve()
    d = (root / sid).resolve()
    if d.parent != root or not d.is_dir():
        raise KeyError(f"no session {sid}")
    return d


def sessions(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    card = api_cards.card_of(conn, m.group(1), m.group(2))
    if card["kind"] != "agent":
        return {"sessions": []}
    try:
        dirs = [d for d in (components.sessions_dir() / card["name"]).iterdir()
                if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        dirs = []
    out = []
    for d in dirs:
        review = next((ln.strip() for ln in _read(d / "review.md").splitlines() if ln.strip()), "")
        out.append({"id": d.name, "when": _when(d), "review": review, "tainted": bool(_taint(d))})
    return {"sessions": sorted(out, key=lambda s: (s["when"], s["id"]), reverse=True)}


def _emotions(d: Path) -> List[Dict[str, Any]]:
    out = []
    for line in _read(d / "emotions.jsonl").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append({"t": e.get("t"), "emotion": str(e.get("emotion", "")), "why": str(e.get("why", ""))})
    return out


def session(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    d = _session(conn, m.group(1), m.group(2), m.group(3))
    notes = [ln.strip()[2:].strip() if ln.strip().startswith("- ") else ln.strip()
             for ln in _read(d / "notes.md").splitlines() if ln.strip()]
    shots = d / "screenshots"
    try:
        names = sorted(p.relative_to(shots).as_posix() for p in shots.rglob("*") if p.is_file())
    except OSError:
        names = []
    taint = _taint(d)
    return {"id": d.name, "when": _when(d), "review": _read(d / "review.md").strip(), "notes": notes,
            "emotions": _emotions(d), "tainted": bool(taint), "taint": taint,
            "screenshots": names}


def _inside(d: Path, base: Path, rel: str) -> Optional[Path]:
    """base/rel resolved when it is a file inside the session folder d, else None."""
    p = (base / rel).resolve()
    return p if d in p.parents and p.is_file() else None


def session_file(conn: sqlite3.Connection, m: Any, q: dict, body: dict) -> Dict[str, Any]:
    d = _session(conn, m.group(1), m.group(2), m.group(3))
    rel = str(q.get("name") or "")
    if not rel or rel.startswith(("/", "~")) or "\x00" in rel:
        raise KeyError(f"no file {rel}")
    p = _inside(d, d, rel) or _inside(d, d / "screenshots", rel)  # a screenshot by its bare name too
    if p is None:
        raise KeyError(f"no file {rel}")
    kind = mimetypes.guess_type(p.name)[0] or "text/plain"
    if kind.startswith("image/"):
        if p.stat().st_size > MAX_IMAGE:
            raise ValueError(f"{rel} is over 5 MB")
        data = base64.b64encode(p.read_bytes()).decode()
        return {"name": rel, "type": kind, "data_url": f"data:{kind};base64,{data}"}
    return {"name": rel, "type": kind, "text": _read(p)}


_CARD = r"/api/projects/([\w.-]+)/cards/([\w.-]+)/sessions"

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = [
    ("GET", _CARD, sessions),
    ("GET", _CARD + r"/([^/]+)", session),
    ("GET", _CARD + r"/([^/]+)/file", session_file),
]
