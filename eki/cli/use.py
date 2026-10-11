"""`eki use [name args…]` — call a component; alone, list what's reachable from here.

A call is a run through the engine: provider 'component' (built in,
pinned), priority 'now', parent the caller's run (EKI_RUN), on a thread
of its own titled `use <name>` with no folder (eki's own, not a chat).
While it can't start, each new reason (runs.why) goes to stderr; at the
end the result text and each output path go to stdout.

`eki use <picture card> --help` prints what the card takes (picture.help_text)
without starting a run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from .. import components, db, store
from . import common

NAME = "use"
HELP = "call a component (eki use alone lists what's reachable from here)"
POLL = 0.3


def add(p) -> None:
    p.add_argument("name", nargs="?", help="the component to call")
    p.add_argument("args", nargs=argparse.REMAINDER, help="passed to the component")


def create(conn, name: str, args: List[str], caller_dir: str, caller_run: Optional[str]) -> str:
    """The call run, written down before anything starts it."""
    prompt = json.dumps({"component": name, "args": list(args), "caller_dir": caller_dir,
                         "caller_run": caller_run})
    with db.tx(conn):
        tid = store.create_thread(conn, f"use {name}", None, by="eki")
        return store.create_run(conn, tid, prompt, provider="component", priority="now", parent=caller_run)


def wait(conn, rid: str, name: str, poll: float = POLL) -> int:
    """Wait for the call to end; say each new reason it waits; print its result."""
    why = None
    try:
        while True:
            r = store.run(conn, rid)
            if r["why"] and r["why"] != why:
                why = r["why"]
                common.err(f"eki use {name}: {why}")
            if r["state"] in common.TERMINAL:
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        conn.execute("UPDATE runs SET cancel=1 WHERE id=?", (rid,))
        common.err(f"eki use {name}: stopped")
        return 130
    if r["state"] != "done":
        common.err(f"eki use {name}: {r['state']}: {r['error'] or 'no reason given'}")
        return 1
    text = store.answer(conn, rid)
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    seen = set()
    for ev in store.events_after(conn, rid):
        data = json.loads(ev["data"] or "{}")
        p = data.get("path")
        if ev["kind"] == "tool" and data.get("name") == f"eki use {name}" and p and p not in seen:
            seen.add(p)
            print(p)
    sys.stdout.flush()
    return 0


def help_text(comp) -> str:
    """What a card takes; a picture card lists its workflow's inputs."""
    from ..components import picture
    if comp.kind == "service" and "target" in comp.spec and not any(k in comp.spec for k in ("run", "serve")):
        return picture.help_text(comp)
    return f"{comp.name} — {comp.spec.get('about', '')}\n({comp.kind}: its words are passed as they are)\n"


def run(args) -> int:
    conn = db.connect()
    here = os.getcwd()
    if args.name:
        try:
            comp = components.get(args.name, here, conn)
        except KeyError as e:
            common.err(f"eki use {args.name}: {e.args[0] if e.args else e}")
            return 1
        if list(args.args) in (["--help"], ["-h"]):
            sys.stdout.write(help_text(comp))
            return 0
        rid = create(conn, args.name, args.args, os.path.abspath(here),
                     os.environ.get("EKI_RUN") or None)
        common.ensure_engine(quiet=True)
        return wait(conn, rid, args.name)
    rows = components.listing(here, conn)
    good = [r for r in rows if not r["error"]]
    for r in good:
        print(f"{r['name']:<16} {r['kind']:<8} {r['scope']:<8} {r['about']}")
    for r in rows:
        if r["error"]:
            print(f"broken: {r['folder']}: {r['error']}", file=sys.stderr)
    if not good:
        print("no components reachable from here")
    return 0
