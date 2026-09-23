# SPDX-License-Identifier: Apache-2.0
"""eki watching itself (ROADMAP, "What eki keeps current about itself").

Two halves, kept apart on purpose:

- **A journal** of what eki notices while it works — nothing is decided
  from it on the spot, no model reads it here, and writing it costs nothing:

  | kind     | what                                                              |
  |----------|-------------------------------------------------------------------|
  | fault    | an error whose traceback ends inside eki's own code                |
  | failed   | a provider said no (quota, login, the CLI refused) — not eki's bug |
  | friction | you corrected an answer, asked again after a failure, stopped a run|
  | gap      | a request nothing could take, or a bare model stood in for a harness|
  | history  | a swap that was rolled back, a learned skill you removed           |

  Friction, gaps and history are for a person to read (and, later, for a
  weekly note that suggests features — suggesting, never building).

- **Fix proposals.** A fault has a right answer and a test that shows it, so
  eki writes a fix for it itself — as a proposal (`eki self`: its own
  worktree, the tests, the candidate check), never applied. A fault is
  grouped by where it happens (exception type, innermost eki file and
  function), so forty of the same are one. It is proposed when it has
  happened twice, or once if it broke one of the engine's own loops; never
  twice in a week for the same fault, a few a day at most, one at a time.
  A fault inside the self-work machinery or a protected path goes to you,
  not back into the loop that would have to fix it.

Setting `self_fix`: "propose" (default) or "off". `eki observe` shows the
journal and the proposals.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path("~/.eki/observe").expanduser()
PACKAGE = str(Path(__file__).resolve().parent)
#: a fault is proposed for once it has happened this often (engine loops: once)
THRESHOLD = 2
#: fix proposals eki starts on its own per day
DAILY = 3
#: the same fault is proposed for at most once in this long
QUIET_DAYS = 7
#: the journal keeps this many lines
KEEP = 5000
#: whose faults are never handed back to self-work (they'd have to fix themselves)
SELF_MACHINERY = ("selfwork.py", "candidate.py", "builds.py", "supervisor.sh", "observe.py", "agent.py",
                  "selfloop.py", "selfengine.py", "roadmap.py")

_lock = threading.Lock()


# ---- the journal -----------------------------------------------------------------

def journal_path() -> Path:
    return HOME / "journal.jsonl"


def note(kind: str, **fields: Any) -> Dict[str, Any]:
    """One line in the journal. Never raises: noticing must not break work."""
    entry = {"at": int(time.time()), "kind": kind,
             **{k: v for k, v in fields.items() if v not in (None, "", [], {})}}
    try:
        with _lock:
            HOME.mkdir(parents=True, exist_ok=True)
            with open(journal_path(), "a") as f:
                f.write(json.dumps(entry) + "\n")
            _trim()
    except OSError:
        pass
    return entry


def _trim() -> None:
    p = journal_path()
    try:
        if p.stat().st_size < 4_000_000:
            return
        lines = p.read_text().splitlines()[-KEEP:]
        p.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def entries(since: float = 0, kind: str = "") -> List[Dict[str, Any]]:
    try:
        lines = journal_path().read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("at", 0) >= since and (not kind or e.get("kind") == kind):
            out.append(e)
    return out


# ---- what a fault is -------------------------------------------------------------

def _frames(tb: Any) -> List[traceback.FrameSummary]:
    return list(traceback.extract_tb(tb)) if tb is not None else []


def ours(frame: traceback.FrameSummary) -> bool:
    try:
        return os.path.realpath(frame.filename).startswith(PACKAGE + os.sep)
    except (TypeError, ValueError):
        return False


def where(exc: BaseException) -> Optional[traceback.FrameSummary]:
    """The innermost frame in eki's own code — None if the error never passed
    through eki (then it isn't eki's fault)."""
    inner = [f for f in _frames(exc.__traceback__) if ours(f)]
    return inner[-1] if inner else None


def is_fault(exc: BaseException) -> bool:
    """An error in eki's code, as opposed to a provider saying no."""
    from .adapters.base import BackendError
    if isinstance(exc, (BackendError, KeyboardInterrupt, SystemExit, GeneratorExit)):
        return False
    import asyncio
    if isinstance(exc, asyncio.CancelledError):
        return False
    return where(exc) is not None


def signature(exc: BaseException) -> str:
    """Where a fault happens, without the line number (edits move lines)."""
    f = where(exc)
    rel = os.path.relpath(f.filename, os.path.dirname(PACKAGE)) if f else "?"
    return f"{type(exc).__name__} in {rel}:{f.name if f else '?'}"


def fault(exc: BaseException, *, source: str, run: str = "", conversation: str = "",
          backend: str = "", request: str = "", log_line: str = "") -> Optional[Dict[str, Any]]:
    """Journal a fault, if it is one. Returns the entry."""
    if not is_fault(exc):
        return None
    f = where(exc)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return note("fault", signature=signature(exc), source=source, error=f"{type(exc).__name__}: {exc}"[:300],
                file=os.path.relpath(f.filename, os.path.dirname(PACKAGE)) if f else "",
                line=f.lineno if f else 0, traceback=tb[-6000:], run=run,
                conversation=conversation, backend=backend, request=(request or "")[:500],
                log=log_line[:300], versions=versions())


_VERSIONS: Dict[str, Any] = {}


def versions() -> Dict[str, str]:
    """Which eki, Claude Code and Codex were running — "broke right after
    Codex 0.155" is most of a diagnosis. Cached for ten minutes."""
    if _VERSIONS.get("at", 0) > time.time() - 600:
        return dict(_VERSIONS["v"])
    import shutil
    import subprocess
    v: Dict[str, str] = {}
    try:
        from . import builds
        info = builds.running()
        v["eki"] = f"{info.get('id')}@{(info.get('commit') or '')[:10]}"
    except Exception:                               # noqa: BLE001
        pass
    for name in ("claude", "codex"):
        path = shutil.which(name) or os.path.expanduser(f"~/.local/bin/{name}")
        if not os.access(path, os.X_OK):
            continue
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"\d+\.\d+\.\d+", out)
            if m:
                v[name] = m.group(0)
        except (OSError, subprocess.SubprocessError):
            pass
    _VERSIONS.update(at=time.time(), v=v)
    return v


class FaultLog(logging.Handler):
    """Catches `log.exception(…)` from the engine's own loops and a request
    handler's crash (uvicorn logs those) — the faults nobody's run saw."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._inside = threading.local()
        #: given each fault entry (the engine may propose a fix)
        self.on_fault: Optional[Any] = None

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._inside, "busy", False) or not record.exc_info:
            return
        exc = record.exc_info[1]
        if exc is None:
            return
        self._inside.busy = True
        try:
            source = "engine" if record.name == "eki" else "request"
            entry = fault(exc, source=source, log_line=record.getMessage())
            if entry is not None and self.on_fault is not None:
                self.on_fault(entry)
        except Exception:                           # noqa: BLE001
            pass
        finally:
            self._inside.busy = False


def install_log_handler() -> FaultLog:
    handler = FaultLog()
    for name in ("eki", "uvicorn.error"):
        logging.getLogger(name).addHandler(handler)
    return handler


# ---- deciding what to propose ------------------------------------------------------

def proposals_path() -> Path:
    return HOME / "proposals.json"


def proposals() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(proposals_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_proposals(data: Dict[str, Dict[str, Any]]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = proposals_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(proposals_path())


def mark(sig: str, **fields: Any) -> None:
    with _lock:
        data = proposals()
        data[sig] = {**data.get(sig, {}), **fields}
        _save_proposals(data)


def due(entry: Dict[str, Any], now: Optional[float] = None,
        threshold: int = THRESHOLD, daily: int = DAILY) -> str:
    """Whether this fault should get a fix proposal now: "" if so, else why not."""
    now = now or time.time()
    sig = entry.get("signature") or ""
    base = os.path.basename(entry.get("file") or "")
    if base in SELF_MACHINERY:
        return "in the self-work machinery — for a person"
    from . import selfwork
    if selfwork.touches_protected([entry.get("file") or ""]):
        return "in a protected path — for a person"
    seen = [e for e in entries(now - QUIET_DAYS * 86400, "fault") if e.get("signature") == sig]
    need = 1 if entry.get("source") == "engine" else threshold
    if len(seen) < need:
        return f"seen {len(seen)} of {need} times"
    known = proposals()
    last = known.get(sig) or {}
    if last.get("state") in ("working", "queued"):
        return "a proposal for it is being written"
    if last.get("at", 0) > now - QUIET_DAYS * 86400:
        return "proposed for this week already"
    today = [p for p in known.values() if p.get("at", 0) > now - 86400]
    if len(today) >= daily:
        return f"{daily} proposals today already"
    return ""


def brief(entry: Dict[str, Any], history: List[Dict[str, Any]], recent: str) -> str:
    """What the agent is asked to do. Goes through `eki self`, which adds how
    to work in eki's source (tests, no commits, no questions)."""
    count = len(history)
    runs = sorted({e.get("run") for e in history if e.get("run")})[:5]
    lines = [
        f"Fix a fault eki observed in itself: {entry.get('error')}",
        f"It happened {count} time(s) in the last {QUIET_DAYS} days, at {entry.get('file')} "
        f"line {entry.get('line')} (source: {entry.get('source')}"
        + (f"; log: {entry['log']}" if entry.get("log") else "") + ").",
    ]
    if entry.get("backend") or entry.get("request"):
        lines.append(f"The run was on {entry.get('backend') or '?'}, asked: "
                     f"{(entry.get('request') or '')[:300]!r}")
    if runs:
        lines.append("Runs it happened in: " + ", ".join(runs))
    if entry.get("versions"):
        lines.append("Versions at the time: " + ", ".join(f"{k} {v}" for k, v in entry["versions"].items()))
    lines += ["", "The traceback:", "```", (entry.get("traceback") or "").strip()[-4000:], "```"]
    if recent:
        lines += ["", f"Recent commits touching {entry.get('file')}:", recent]
    lines += ["",
              "Find the cause, fix it at the root (not by swallowing the error), and add a "
              "test that fails without the fix. If an external program's output changed "
              "(a CLI update), handle both the old and the new shape. If the right fix "
              "isn't clear from the code, make the smallest safe change and explain what "
              "you'd check next."]
    return "\n".join(lines)


def recent_commits(root: Path, file: str) -> str:
    import subprocess
    if not file:
        return ""
    try:
        return subprocess.run(["git", "-C", str(root), "log", "-5", "--format=%h %ad %s",
                               "--date=short", "--", file], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def summary(days: float = 7) -> Dict[str, Any]:
    since = time.time() - days * 86400
    rows = entries(since)
    kinds: Dict[str, int] = {}
    for e in rows:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    faults: Dict[str, Dict[str, Any]] = {}
    for e in rows:
        if e["kind"] != "fault":
            continue
        f = faults.setdefault(e["signature"], {"signature": e["signature"], "count": 0,
                                               "error": e.get("error"), "first": e["at"]})
        f["count"] += 1
        f["last"] = e["at"]
    props = proposals()
    for sig, f in faults.items():
        if sig in props:
            f["proposal"] = props[sig]
    return {"days": days, "kinds": kinds, "faults": sorted(faults.values(), key=lambda f: -f["last"]),
            "recent": rows[-30:][::-1]}
