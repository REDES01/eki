# SPDX-License-Identifier: Apache-2.0
"""`eki backends|history|show|cost|summary`: reading straight from the
database and the config — no engine needed."""
from __future__ import annotations

import asyncio
import sys
import time

from .. import config as config_mod
from ..engine import Engine
from ..store import Store

ORDER = 70


def register(sub) -> None:
    sub.add_parser("backends", help="list backends and health").set_defaults(
        func=lambda args: asyncio.run(cmd_backends(config_mod.load(args.config))))
    h = sub.add_parser("history", help="recent conversations")
    h.add_argument("-n", "--limit", type=int, default=20)
    h.add_argument("-q", "--query", default="", help="only ones containing this text")
    h.set_defaults(func=lambda args: cmd_history(config_mod.load(args.config), args.limit, args.query))
    sh = sub.add_parser("show", help="print one conversation")
    sh.add_argument("id")
    sh.set_defaults(func=lambda args: cmd_show(config_mod.load(args.config), args.id))
    co = sub.add_parser("cost", help="what a conversation spent")
    co.add_argument("id")
    co.set_defaults(func=lambda args: cmd_cost(config_mod.load(args.config), args.id))
    su = sub.add_parser("summary", help="a long thread's summary, as the next model reads it")
    su.add_argument("id")
    su.set_defaults(func=lambda args: cmd_summary(config_mod.load(args.config), args.id))


async def cmd_backends(cfg) -> int:
    eng = Engine(cfg)
    for b in await eng.describe():
        caps = b["capabilities"]
        flags = ",".join(n for n, on in (
            ("repo", caps.get("repo")), ("tools", caps.get("tools")),
            ("vision", caps.get("vision")), ("images", caps.get("images_out")),
        ) if on) or "chat"
        made = "+".join(caps.get("produces") or [])
        if made:
            flags += f" [{made}]"
        mark = "ok  " if b["ok"] else "down"
        print(f"{mark} {b['key']:10} tier {b['tier']:<4} {flags:34} {b['detail']}")
    await eng.close()
    return 0


def cmd_history(cfg, limit: int, query: str = "") -> int:
    store = Store(cfg.db)
    rows = store.search(query, limit) if query else store.conversations(limit)
    if not rows:
        print(f"nothing matching {query!r}" if query else "no conversations yet")
        return 0
    for r in rows:
        title = (r["title"] or "").replace("\n", " ")[:56]
        print(f"{r['id']}  {r['n']:>3} turns  {title}")
        hit = (r.get("hit") or "").replace("\n", " ").strip()
        if query and hit and hit[:56] != title:
            print(f"                     …{hit[:70]}")
    return 0


def cmd_show(cfg, cid: str) -> int:
    turns = Store(cfg.db).turns(cid)
    if not turns:
        print(f"no such conversation: {cid}", file=sys.stderr)
        return 1
    for t in turns:
        who = t["role"] if t["role"] == "user" else f"{t['role']} ({t['backend']})"
        print(f"\n--- {who} ---")
        print(t["content"])
    return 0


def cmd_summary(cfg, cid: str) -> int:
    """What a long thread's older part was summarized to, for the next
    model to read (eki/carry.py) — newest last."""
    store = Store(cfg.db)
    if not store.turns(cid):
        print(f"no such conversation: {cid}", file=sys.stderr)
        return 1
    kept = store.summaries(cid)
    if not kept:
        print("no summary: every model so far has read this thread whole")
        return 0
    for s in kept:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["created_at"]))
        print(f"\n--- up to turn {s['upto']}, by {s['author'] or 'unknown'}, {when} ---")
        print(s["text"])
    return 0


def cmd_cost(cfg, cid: str) -> int:
    report = Store(cfg.db).cost(cid)
    if not report["by_backend"]:
        print(f"no answered turns in {cid}", file=sys.stderr)
        return 1
    notes = {b.key: b.cost.note for b in cfg.backends}
    print(f"{report['turns']} answered turns")
    for key, entry in sorted(report["by_backend"].items(),
                             key=lambda kv: -kv[1]["turns"]):
        tokens = ""
        if entry["input_tokens"] or entry["output_tokens"]:
            tokens = (f"  {entry['input_tokens']:>8} in / "
                      f"{entry['output_tokens']:>6} out")
        print(f"  {key:10} {entry['turns']:>3} turns  {notes.get(key, '')}{tokens}")
    return 0
