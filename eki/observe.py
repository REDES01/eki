"""The journal: what happened to eki, written down as it happens.

Faults (tracebacks in eki's own code), handoffs, corrections (you said
"no, …" or asked again), limits hit, and the end of every run — enough to
compute the score (eki/score.py) from the journal alone. This module is the
only writer of the `journal` table, and it never raises: the journal must
not break what it watches. Nothing secret is written down.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
import traceback
from typing import Any, Dict, List, Optional, Tuple

from . import db

log = logging.getLogger("eki.observe")

KINDS = ("fault", "handoff", "correction", "limit", "run", "regression")

TB_MAX = 4000                 # a traceback is cut to its last this many chars
CORRECTION_WINDOW = 600.0     # seconds after the previous run
REDO_RATIO = 0.9
CORRECTION_WORDS = ("i meant", "actually", "instead", "wrong", "not", "no")

_SECRETS = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]+", re.I),
]
_KEYED = re.compile(
    r"(?P<key>\b[\w\-]*(?:token|secret|password|passwd|api[_\-]?key|apikey|authorization)[\w\-]*"
    r"[\"']?\s*[:=]\s*)(?P<q>[\"']?)(?P<val>[^\s\"',;]+)", re.I)
_FILE = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)', re.M)
_CORRECTION = re.compile(r"^(?:%s)\b" % "|".join(re.escape(w) for w in CORRECTION_WORDS), re.I)


def scrub(text: str) -> str:
    """Replace what looks like a secret with '[secret]'."""
    if not isinstance(text, str) or not text:
        return text
    for pat in _SECRETS:
        text = pat.sub("[secret]", text)
    return _KEYED.sub(lambda m: m.group("key") + m.group("q") + "[secret]", text)


def _scrub_all(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_all(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_all(v) for v in value]
    return value


def _build() -> Optional[str]:
    try:
        from . import builds
        return builds.running_id()
    except Exception:
        return None


def record(conn: Optional[sqlite3.Connection], kind: str, *, run_id: Optional[str] = None,
           thread_id: Optional[str] = None, provider: Optional[str] = None,
           build: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Write one row; no transaction of its own, so it works inside db.tx or out of one."""
    own = None
    try:
        if conn is None:
            conn = own = db.connect()
        cur = conn.execute(
            "INSERT INTO journal(t, kind, run_id, thread_id, provider, build, data)"
            " VALUES (?,?,?,?,?,?,?)",
            (db.now(), kind, run_id, thread_id, provider, build if build is not None else _build(),
             db.dumps(_scrub_all(data or {}))))
        return int(cur.lastrowid)
    except Exception:
        log.exception("journal: could not record %s", kind)
        return None
    finally:
        if own is not None:
            try:
                own.close()
            except Exception:
                pass


# ---- faults ----------------------------------------------------------------------------

def _eki_relative(path: str) -> Optional[str]:
    """'…/builds/<id>/eki/x.py' → 'eki/x.py'; None for a file outside an eki/ package."""
    parts = path.replace("\\", "/").split("/")
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "eki" and parts[-1].endswith(".py"):
            return "/".join(parts[i:])
    return None


def top_frame(tb: str) -> Optional[Tuple[str, int]]:
    """The innermost frame of the traceback that is in eki's own code."""
    found = None
    for m in _FILE.finditer(tb or ""):
        rel = _eki_relative(m.group("path"))
        if rel is not None:
            found = (rel, int(m.group("line")))
    return found


def _exc_name(tb: str) -> Optional[str]:
    for line in reversed((tb or "").strip().splitlines()):
        if line and not line[0].isspace():
            name = line.split(":", 1)[0].strip()
            return name or None
    return None


def fault(conn: Optional[sqlite3.Connection], where: str, tb: Optional[str] = None, *,
          run_id: Optional[str] = None, thread_id: Optional[str] = None) -> Optional[int]:
    """A traceback, written down with where it was caught and the frame it came from."""
    try:
        if tb is None:
            tb = traceback.format_exc()
        frame = top_frame(tb)
        data = {"where": where, "traceback": tb[-TB_MAX:],
                "frame": f"{frame[0]}:{frame[1]}" if frame else None,
                "exc": _exc_name(tb), "ours": frame is not None}
    except Exception:
        log.exception("journal: could not read a traceback")
        data = {"where": where, "traceback": None, "frame": None, "exc": None, "ours": False}
    return record(conn, "fault", run_id=run_id, thread_id=thread_id, data=data)


# ---- runs ------------------------------------------------------------------------------

def _is_local(provider: Optional[str]) -> bool:
    try:
        from . import providers
        return providers.config().get(provider or "", {}).get("kind") == "local"
    except Exception:
        return False


def run_ended(conn: sqlite3.Connection, rid: str) -> Optional[int]:
    """One 'run' row for an ended run: what the score is computed from."""
    try:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if r is None or r["ended_at"] is None:
            return None
        if conn.execute("SELECT 1 FROM journal WHERE kind='run' AND run_id=?", (rid,)).fetchone():
            return None
        first = conn.execute("SELECT MIN(t) FROM events WHERE run_id=? AND kind='text'",
                             (rid,)).fetchone()[0]
        data = {"state": r["state"], "seconds": round(r["ended_at"] - r["created_at"], 3),
                "local": _is_local(r["provider"]), "handed_off": r["state"] == "handed_off",
                "first_text": round(first - r["created_at"], 3) if first is not None else None}
    except Exception:
        log.exception("journal: could not read run %s", rid)
        return None
    return record(conn, "run", run_id=rid, thread_id=r["thread_id"], provider=r["provider"],
                  data=data)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def check_correction(conn: sqlite3.Connection, rid: str) -> bool:
    """Was this run you saying the last answer was wrong (or asking it again)?"""
    try:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if r is None or r["parent"] is not None or r["provider"] == "command":
            return False
        tid = r["thread_id"]
        if conn.execute("SELECT 1 FROM goals WHERE thread_id=? UNION ALL "
                        "SELECT 1 FROM items WHERE thread_id=? LIMIT 1", (tid, tid)).fetchone():
            return False
        prev = conn.execute(
            "SELECT * FROM runs WHERE thread_id=? AND seq<? AND parent IS NULL"
            " AND (provider IS NULL OR provider!='command') ORDER BY seq DESC LIMIT 1",
            (tid, r["seq"])).fetchone()
        if prev is None or r["created_at"] - prev["created_at"] > CORRECTION_WINDOW:
            return False
        said = (r["prompt"] or "").strip()
        redo = difflib.SequenceMatcher(None, _norm(said), _norm(prev["prompt"])).ratio() >= REDO_RATIO
        if not redo and not _CORRECTION.match(said):
            return False
    except Exception:
        log.exception("journal: could not check run %s", rid)
        return False
    record(conn, "correction", run_id=rid, thread_id=tid, provider=r["provider"],
           data={"previous": prev["id"], "prompt": said[:200], "redo": redo})
    return True


# ---- reading ---------------------------------------------------------------------------

def entries(conn: sqlite3.Connection, *, since: float = 0, until: Optional[float] = None,
            kind: Optional[str] = None) -> List[Dict[str, Any]]:
    sql, args = "SELECT * FROM journal WHERE t>=?", [since]
    if until is not None:
        sql += " AND t<?"
        args.append(until)
    if kind is not None:
        sql += " AND kind=?"
        args.append(kind)
    out = []
    for row in conn.execute(sql + " ORDER BY t, id", args):
        e = dict(row)
        try:
            e["data"] = json.loads(e["data"] or "{}")
        except ValueError:
            e["data"] = {}
        out.append(e)
    return out


def prune(conn: sqlite3.Connection, days: float = 90) -> int:
    """Forget what is older than `days`."""
    return conn.execute("DELETE FROM journal WHERE t<?", (db.now() - days * 86400,)).rowcount


def parse_since(text: str) -> float:
    """'30m' / '24h' / '7d' → seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([mhd])\s*", text or "")
    if not m:
        raise ValueError(f"not a span like 30m, 24h or 7d: {text!r}")
    return float(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
