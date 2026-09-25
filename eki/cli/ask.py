"""Ask for something. It becomes a run, and eki follows it until it's done."""
from __future__ import annotations

import os
import sys

from .. import db, providers, store
from .common import conn, ensure_engine, err, follow

NAME = "ask"
HELP = "ask for something; eki routes it, runs it and shows the answer"


def add(p) -> None:
    p.add_argument("prompt", nargs="+", help="the request ('-' reads stdin)")
    p.add_argument("-t", "--thread", help="continue this thread")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the last thread")
    p.add_argument("--to", help="send it to this provider (claude, codex, local …)")
    p.add_argument("-C", "--cwd", help="work in this folder")
    p.add_argument("--bg", action="store_true", help="don't wait: print the run id and return")
    p.add_argument("--background", action="store_true",
                   help="background priority: runs only when the Mac has room")
    p.add_argument("-q", "--quiet", action="store_true", help="no tool lines")


def run(args) -> int:
    prompt = " ".join(args.prompt)
    if prompt == "-":
        prompt = sys.stdin.read()
    if args.to and args.to not in providers.config():
        raise KeyError(f"no provider named {args.to!r} (see eki-next providers)")
    c = conn()
    cwd = os.path.abspath(os.path.expanduser(args.cwd)) if args.cwd else None
    with db.tx(c):
        tid = None
        if args.thread:
            t = store.thread(c, args.thread)
            if t is None:
                raise KeyError(f"no thread {args.thread}")
            tid = t["id"]
        elif args.cont:
            last = c.execute("SELECT thread_id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
            tid = last[0] if last else None
        if tid is None:
            tid = store.create_thread(c, prompt.strip().splitlines()[0] if prompt.strip() else "", cwd)
        elif cwd:
            c.execute("UPDATE threads SET cwd=? WHERE id=?", (cwd, tid))
        rid = store.create_run(c, tid, prompt, provider=args.to,
                               priority="background" if args.background else "now")
    ensure_engine(quiet=args.bg)
    if args.bg:
        print(rid)
        return 0
    try:
        code = follow(c, rid, show_tools=not args.quiet)
    except KeyboardInterrupt:
        err(f"\n(still running — eki-next follow {rid}; stop it with eki-next cancel {rid})")
        return 130
    err(f"[thread {tid} · run {rid}]")
    return code
