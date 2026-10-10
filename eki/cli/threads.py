"""Threads: your conversations (eki's own with --all), one printed whole, or acted on."""
from __future__ import annotations

import sys

from .. import api_threads, store, threadacts
from .common import ago, conn

NAME = "threads"
HELP = "list threads, print one as a conversation, or pin/rename/archive/delete one"

#: an action name and the threadacts.update body it sends (delete and rename apart)
ACTIONS = {"pin": {"pin": True}, "unpin": {"pin": False},
           "archive": {"archive": True}, "unarchive": {"archive": False},
           "rename": None, "delete": None}
ASK_DELETE = "Delete this conversation? Files it changed stay. [y/N] "


def add(p) -> None:
    p.add_argument("thread", nargs="?", help="a thread id, or an action: " + "|".join(ACTIONS))
    p.add_argument("rest", nargs="*", help="with an action: the thread id, then a title for rename")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--all", action="store_true", help="eki's own threads too")
    p.add_argument("-y", action="store_true", help="delete without asking")


def _live(c, tid: str):
    t = store.thread(c, tid) if tid else None
    if t is None or t["deleted_at"] is not None:
        raise KeyError(f"no thread {tid}")
    return t


def _act(c, args) -> int:
    action = args.thread
    if not args.rest:
        raise ValueError(f"eki threads {action} <id>")
    t = _live(c, args.rest[0])
    if action == "delete":
        if not args.y and sys.stdin.isatty():
            if input(ASK_DELETE).strip().lower() not in ("y", "yes"):
                print("kept")
                return 0
        n = threadacts.delete(c, t["id"])
        print(f"deleted {t['id']}" + (f" · stopping {n} run{'s' if n != 1 else ''}" if n else ""))
        return 0
    body = ACTIONS[action] or {"title": " ".join(args.rest[1:])}
    threadacts.update(c, t["id"], body)
    print(f"{action} {t['id']}")
    return 0


def _show(c, tid: str) -> int:
    t = _live(c, tid)
    print(f"thread {t['id']} · stays with {t['provider'] or '-'} · {t['cwd'] or 'no folder'}\n")
    for r in store.thread_runs(c, t["id"]):
        print(f"> {r['prompt']}\n")
        print(f"[{r['provider'] or '-'} · {r['state']}]")
        said = store.answer(c, r["id"])
        if said:
            print(said.rstrip() + "\n")
    return 0


def run(args) -> int:
    c = conn()
    if args.thread in ACTIONS:
        return _act(c, args)
    if args.thread:
        return _show(c, args.thread)
    for e in api_threads.threads(c, limit=args.n, all=args.all):
        mark = ("*" if e["pinned"] else " ") + ("●" if e["unread"] else " ")
        print(f"{e['id']} {mark} {e['provider'] or '-':<8} {ago(e['updated']):>8}  {e['title'][:56]}")
    return 0
