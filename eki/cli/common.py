# SPDX-License-Identifier: Apache-2.0
"""What every command shares: reaching the engine, calling it, following a run.

A command lives in a module of its own in this package (see __init__.py);
only what more than one of them needs belongs here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

from .. import agent
from ..runs import TERMINAL

DEFAULT_SERVICE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent.parent
#: the thread a request comes from, when a program eki started makes it (service.py)
PARENT_HEADER = "X-Eki-Parent"


# ---- reaching the engine ---------------------------------------------

def engine_up(service: str) -> bool:
    try:
        return httpx.get(f"{service}/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_engine(service: str) -> None:
    """Make sure something is listening, starting it if need be.

    With the login agent installed this only ever has to nudge launchd;
    without it, it starts a detached engine that outlives this command.
    """
    if engine_up(service):
        return
    if agent.installed():
        agent.restart()
    else:
        subprocess.Popen(
            [sys.executable, "-m", "eki.cli", "serve"], cwd=str(ROOT),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        if engine_up(service):
            return
    print(f"! the engine didn't come up on {service} — try `eki serve` to see why",
          file=sys.stderr)
    raise SystemExit(1)


def parent_headers() -> Dict[str, str]:
    """Run from inside a thread's program (EKI_PARENT, set by the engine): the
    engine hears whose work is asking, and refuses eki's own work what only
    the person may do."""
    parent = os.environ.get("EKI_PARENT", "")
    return {PARENT_HEADER: parent} if parent else {}


def call(method: str, path: str, service: str, **kw) -> Any:
    ensure_engine(service)
    kw["headers"] = {**(kw.get("headers") or {}), **parent_headers()}
    try:
        r = httpx.request(method, service + path, timeout=30, **kw)
    except httpx.HTTPError as e:
        print(f"! lost the engine: {e}", file=sys.stderr)
        raise SystemExit(1)
    if r.status_code >= 400 and r.status_code != 409:
        try:
            said = str(r.json().get("detail") or r.text)
        except ValueError:
            said = r.text
        print(f"! {r.status_code} {said[:300]}", file=sys.stderr)
        raise SystemExit(1)
    return r.json()


# ---- following a run -------------------------------------------------

def watch(rid: str, service: str, quiet: bool = False, conversation: str = "") -> int:
    """Follow a run until it ends. Ctrl-C lets go of it; it keeps going."""
    ensure_engine(service)
    parts: List[str] = []
    state = ""
    try:
        with httpx.stream("GET", f"{service}/api/runs/{rid}/stream", timeout=None) as r:
            if r.status_code >= 400:
                print(f"! no such run: {rid}", file=sys.stderr)
                return 1
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                kind = event.get("event")
                if kind == "route" and not quiet:
                    print(f"[{event.get('reason')}]", file=sys.stderr)
                elif kind == "output":
                    parts.append(event["text"])
                    if not quiet:
                        sys.stdout.write(event["text"])
                        sys.stdout.flush()
                elif kind == "error":
                    print(f"\n! {event.get('message')}", file=sys.stderr)
                elif kind == "state":
                    state = event.get("state", "")
                    if state in TERMINAL:
                        break
    except httpx.HTTPError as e:
        print(f"\n! lost the engine: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n[let go — it's still running: eki watch {rid}]", file=sys.stderr)
        return 0

    if quiet:
        print("".join(parts))
    elif parts and not parts[-1].endswith("\n"):
        print()
    # stdout is buffered and stderr isn't; without this the status line
    # lands on the same line as the answer's last words
    sys.stdout.flush()
    if not quiet:
        tail = f" · conversation {conversation}" if conversation else ""
        print(f"[{state} · run {rid}{tail}]", file=sys.stderr)
    return 0 if state == "done" else 1


# ---- going live, in a line (eki self, eki builds) ---------------------

def _cut(text: str, n: int) -> str:
    """A title cut to fit a line, at a sentence or a whole word, never
    mid-word (eki/pipeline.py short_title)."""
    from .. import pipeline
    return pipeline.short_title(text, n)


def _next_go_live(t: Dict[str, Any], everything: bool = False) -> str:
    """"next go-live in N min, carrying: …" — the release train (builds.board)."""
    if not t or not t.get("carrying"):
        return ""
    left = int(t.get("in") or 0)
    when = "now" if left <= 0 else f"in {max(1, -(-left // 60))} min"
    cars = t["carrying"] if everything else t["carrying"][:6]
    what = ", ".join(f"self/{c['id']} ({c.get('title', '') if everything else _cut(c.get('title', ''), 40)})"
                     for c in cars)
    more = f" and {len(t['carrying']) - len(cars)} more (eki self --all)" if len(t["carrying"]) > len(cars) else ""
    return f"next go-live {when}, carrying: {what}{more}   (go now: eki self release)"


def _going_live(g: Dict[str, Any]) -> str:
    """"new version going live in N min" — a swap on its way in (builds.going_live)."""
    if not g:
        return ""
    what = f" (self/{g['self']})" if g.get("self") else ""
    if g.get("state") == "swapping" or not g.get("in"):
        return f"new version{what} going live now"
    mins = max(1, -(-int(g["in"]) // 60))
    return (f"new version{what} going live in {mins} min — at once if nothing is running; "
            "runs still going then carry on in it")
