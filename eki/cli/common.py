"""Shared by the commands: opening the store, keeping an engine up, following a run."""
from __future__ import annotations

import json
import sys
import time
from typing import Optional

from .. import db, engine, store

TERMINAL = ("done", "failed", "cancelled", "handed_off")


def conn():
    return db.connect()


def ensure_engine(quiet: bool = False) -> None:
    if engine.running_pid() is None:
        pid = engine.start_detached()
        if not quiet:
            print(f"(started the engine, pid {pid})", file=sys.stderr)


def err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def ago(t: Optional[float]) -> str:
    if not t:
        return "-"
    s = int(time.time() - t)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def follow(c, rid: str, *, show_tools: bool = True, poll: float = 0.2) -> int:
    """Print a run as it happens, into the run it hands off to. Exit code: 0 done."""
    last = 0
    wrote_text = False
    while True:
        for ev in store.events_after(c, rid, last):
            last = ev["id"]
            data = json.loads(ev["data"])
            kind = ev["kind"]
            if kind == "text":
                sys.stdout.write(data.get("text", ""))
                sys.stdout.flush()
                wrote_text = True
            elif kind == "route":
                err(f"→ {data['provider']}  ({data['why']})")
            elif kind == "tool" and show_tools:
                err(f"  · {data.get('name')} {data.get('detail', '')}")
            elif kind in ("note", "interrupted"):
                err(f"  ! {data.get('text') or kind}")
            elif kind == "handoff":
                if wrote_text:
                    sys.stdout.write("\n")
                err(f"  ↪ handed off: {data.get('reason') or 'needs tools'}")
                return follow(c, data["to_run"], show_tools=show_tools, poll=poll)
        r = store.run(c, rid)
        if r["state"] in TERMINAL and not store.events_after(c, rid, last):
            if wrote_text:
                sys.stdout.write("\n")
            if r["state"] == "failed":
                err(f"failed: {r['error']}")
                return 1
            if r["state"] == "cancelled":
                err("cancelled")
                return 1
            return 0
        time.sleep(poll)
