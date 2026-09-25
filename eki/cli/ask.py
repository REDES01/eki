"""Ask for something. It becomes a run, and eki follows it until it's done."""
from __future__ import annotations

import sys

from .. import asking, store
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
    c = conn()
    tid, rid = asking.submit(c, prompt, thread=args.thread, continue_last=args.cont, to=args.to,
                             cwd=args.cwd, background=args.background)
    ensure_engine(quiet=args.bg)
    if args.bg:
        print(rid)
        return 0
    try:
        code = follow(c, rid, show_tools=not args.quiet)
    except KeyboardInterrupt:
        err(f"\n(still running — eki follow {rid}; stop it with eki cancel {rid})")
        return 130
    last = store.thread_runs(c, tid)[-1]["id"]
    err(f"[thread {tid} · run {last}]")
    return code
