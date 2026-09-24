# SPDX-License-Identifier: Apache-2.0
"""Every piece of self-work is a step, written down before it starts
(docs/self-build.md, "A restart loses nothing").

    begin     its worktree, and the base passing its own tests
    agent     the agent's turn, in its session
    check     what the agent did, committed and judged (the candidate check)
    apply     its turn in the merge queue: put on top, judged again, built
    resolve   its conflicts resolved by an agent, mid-rebase
    swap      the go-live: the supervisor swapping a build in

A step is *running*, then *succeeded*, *failed* or *interrupted*.
Interrupted — the engine going away, a program killed by a signal (exit
143), a check cut off, a child lost — is never failed. On start the new
engine marks every step the old one left running interrupted
(`recover`), and each is taken up again once (`claim`): an agent step in its
session, a check or a git step re-run from where its change stands — every
step is written so that doing it again does no harm. A step cut off five
times in a row is failed, so a crash can't loop.

On 2026-09-24 eki went live six times in an hour: the agents carried on,
but a conflict-resolving agent killed by the restart was reported as "the
agent couldn't resolve it", a test run cut off as "tests ✗", and a base
check started again from scratch. This is what keeps those apart.

The ledger is ~/.eki/self/steps.json, one entry per step, keyed
"<kind>:<subject>" — the item for the item's own steps, the change for a
resolve, the build for a swap.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

HOME = Path("~/.eki/self").expanduser()
KINDS = ("begin", "agent", "check", "apply", "resolve", "swap")
OUTCOMES = ("succeeded", "failed", "interrupted")
#: cut off this many times in a row, a step is failed — a crash can't loop
MAX_TRIES = 5
#: a step cut off while this engine stays up (a child lost, not a restart)
#: is taken up again after this long — an engine going away is gone by then
LOST_GRACE = 20
#: finished steps are forgotten after a week
KEEP = 7 * 86400
#: steps that run outside the engine (the supervisor): not cut off by a restart
OUTSIDE = ("swap",)
#: this engine, as the ledger knows it: a step started by another is not live
BOOT = uuid.uuid4().hex[:12]

_lock = threading.Lock()


class Interrupted(Exception):
    """The work was cut off, not wrong: carried on or run again, never failed.
    Not a RuntimeError, so a check's `except RuntimeError` (a verdict) never
    takes it for a failing test."""


#: an exit a signal caused — what a program says when it was stopped, not
#: when it gave up: 143/137 (a shell's 128+TERM/KILL), -15/-9 (Python's)
_SIGNAL_EXITS = {143, 137, 130, -15, -9, -2, -1}
_CUT = re.compile(r"\b(?:exit(?:ed)?|code|status)\b[^0-9\n]{0,16}(?:143|137|-15|-9)\b"
                  r"|\bSIG(?:TERM|KILL)\b|\bkilled by signal\b|\bterminated by signal\b", re.I)


def cut_off(what: Union[int, str, BaseException, None]) -> bool:
    """Was this the work being stopped from outside — a signal — rather than
    failing? An exit code, or the words a program or eki said about it."""
    if what is None:
        return False
    if isinstance(what, int):
        return what in _SIGNAL_EXITS or what < 0
    if isinstance(what, Interrupted):
        return True
    return bool(_CUT.search(str(what)))


# ---- the ledger ------------------------------------------------------------------------

def _path(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "steps.json"


def _load(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_path(home).read_text()).get("steps") or {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _save(data: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    now = time.time()
    data = {k: s for k, s in data.items()
            if s.get("state") not in ("succeeded", "failed") or now - float(s.get("at") or 0) < KEEP}
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"steps": data}, indent=2, ensure_ascii=False))
    tmp.replace(path)


def key(kind: str, subject: str) -> str:
    return f"{kind}:{subject}"


def get(kind: str, subject: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """The step, or {} when it was never begun."""
    return dict(_load(home).get(key(kind, subject)) or {})


def steps(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    return sorted(_load(home).values(), key=lambda s: float(s.get("started_at") or 0))


def start(kind: str, subject: str, *, home: Optional[Path] = None, **fields: Any) -> Dict[str, Any]:
    """Written down before the step starts: running, in this engine. A step
    taken up again after a cut (claimed) keeps its count of tries."""
    if kind not in KINDS:
        raise ValueError(f"no step {kind!r}")
    now = int(time.time())
    with _lock:
        data = _load(home)
        k = key(kind, subject)
        was = data.get(k) or {}
        again = was.get("state") in ("running", "interrupted")
        s = {**(was if again else {}), **{f: v for f, v in fields.items() if v is not None},
             "kind": kind, "subject": subject, "state": "running", "boot": BOOT,
             "started_at": now, "at": now, "tries": int(was.get("tries") or 0) if again else 0,
             "note": ""}
        data[k] = s
        _save(data, home)
    return dict(s)


def end(kind: str, subject: str, outcome: str, note: str = "", home: Optional[Path] = None,
        **fields: Any) -> Dict[str, Any]:
    """How the step came out. Interrupted too many times in a row is failed."""
    if outcome not in OUTCOMES:
        raise ValueError(f"no outcome {outcome!r}")
    with _lock:
        data = _load(home)
        k = key(kind, subject)
        s = data.get(k)
        if s is None:
            return {}
        if outcome == "interrupted" and int(s.get("tries") or 0) + 1 >= MAX_TRIES:
            outcome, note = "failed", f"cut off {MAX_TRIES} times in a row — {note}".rstrip(" —")
        s.update({f: v for f, v in fields.items() if v is not None})
        s.update(state=outcome, note=str(note)[:300], at=int(time.time()), ended_at=int(time.time()))
        _save(data, home)
    return dict(s)


def forget(kind: str, subject: str, home: Optional[Path] = None) -> None:
    """A step whose work has moved on (its item was taken back, its change
    decided by hand): nothing left to carry on."""
    with _lock:
        data = _load(home)
        if data.pop(key(kind, subject), None) is not None:
            _save(data, home)


def recover(home: Optional[Path] = None, skip: Iterable[str] = OUTSIDE) -> List[Dict[str, Any]]:
    """At start: every step an earlier engine left running was cut off with
    it — interrupted. Steps that run outside the engine (`skip`) aren't. The
    steps now waiting to be taken up again."""
    skip = set(skip)
    with _lock:
        data = _load(home)
        moved = False
        for s in data.values():
            if s.get("state") == "running" and s.get("boot") != BOOT and s.get("kind") not in skip:
                s.update(state="interrupted", note=s.get("note") or "the engine restarted",
                         at=int(time.time()), ended_at=int(time.time()))
                moved = True
        if moved:
            _save(data, home)
    return [dict(s) for s in data.values() if s.get("state") == "interrupted"]


def unfinished(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    return [s for s in steps(home) if s.get("state") in ("running", "interrupted")]


def live(s: Dict[str, Any], running: Iterable[str] = ()) -> bool:
    """Is this step being worked on right now, in this engine? A step whose
    run is gone, or that an earlier engine started, is not — whatever it says."""
    if not s or s.get("state") != "running" or s.get("boot") != BOOT:
        return False
    return not s.get("run") or s["run"] in set(running)


def waiting(s: Dict[str, Any], running: Iterable[str] = (), now: Optional[float] = None) -> bool:
    """Cut off and not yet taken up: interrupted (or left running by an
    earlier engine, or by a run of this one that is gone) — and, when this
    engine is the one that saw it cut off, a moment has passed, so an engine
    on its way out leaves it to the next."""
    if not s or s.get("state") not in ("running", "interrupted") or live(s, running):
        return False
    if s.get("boot") != BOOT:
        return True
    return (now or time.time()) - float(s.get("at") or 0) >= LOST_GRACE


def claim(kind: str, subject: str, run: str = "", home: Optional[Path] = None) -> bool:
    """Take a cut-off step up again — once. True for the one caller that
    gets it; False when it's live, finished, or already taken up."""
    with _lock:
        data = _load(home)
        s = data.get(key(kind, subject))
        if not s or s.get("state") not in ("running", "interrupted"):
            return False
        if s.get("state") == "running" and s.get("boot") == BOOT:
            return False                    # this engine has it (or a run of it just began)
        tries = int(s.get("tries") or 0) + 1
        if tries >= MAX_TRIES:
            s.update(state="failed", note=f"cut off {MAX_TRIES} times in a row", at=int(time.time()))
            _save(data, home)
            return False
        s.update(state="running", boot=BOOT, run=run or s.get("run") or "", tries=tries,
                 at=int(time.time()))
        _save(data, home)
        return True


def of_item(item: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """The item's latest step — where its pipeline stands."""
    mine = [s for s in steps(home) if s.get("item") == item and s.get("kind") != "resolve"]
    return max(mine, key=lambda s: float(s.get("at") or 0), default={})
