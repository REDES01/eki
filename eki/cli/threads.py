"""Threads: conversations, and who each stays with."""
from __future__ import annotations

from .. import store
from .common import ago, conn

NAME = "threads"
HELP = "list threads, or print one as a conversation"


def add(p) -> None:
    p.add_argument("thread", nargs="?")
    p.add_argument("-n", type=int, default=20)


def run(args) -> int:
    c = conn()
    if args.thread:
        t = store.thread(c, args.thread)
        if t is None:
            raise KeyError(f"no thread {args.thread}")
        print(f"thread {t['id']} · stays with {t['provider'] or '-'} · {t['cwd'] or 'no folder'}\n")
        for r in store.thread_runs(c, t["id"]):
            print(f"> {r['prompt']}\n")
            print(f"[{r['provider'] or '-'} · {r['state']}]")
            said = store.answer(c, r["id"])
            if said:
                print(said.rstrip() + "\n")
        return 0
    rows = c.execute("SELECT * FROM threads ORDER BY created_at DESC LIMIT ?", (args.n,)).fetchall()
    for t in rows:
        print(f"{t['id']}  {t['provider'] or '-':<8} {ago(t['created_at']):>8}  {t['title'][:56]}")
    return 0
