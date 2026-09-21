"""eki — tell it to do something; it keeps doing it until it's done.

    eki ask "why is this slow?"          routed; the answer streams here
    eki ask "..." --backend claude       force a backend
    eki ask "..." --repo .               may edit files there
    eki ask "a brass compass" --image    draw it
    eki ask "..." --continue             same thread as last time
    eki ask "..." --detach               start it and return straight away

Every ask is a run in the engine, not in this terminal: Ctrl-C stops
*watching*, never the work. Pick it back up with `eki watch <run>`.

    eki runs                             what's running and what finished
    eki watch <run>                      follow one, from the start
    eki cancel <run>                     stop it (its CLI child dies with it)
    eki diff <run>                       what it changed in the repo

    eki history [-q text]                conversations, or a search of them
    eki show <conversation>              one thread, with who answered
    eki cost <conversation>              who answered, and what they reported
    eki backends | models | policy       what exists, what's loaded, the rules
    eki agent install|uninstall|status   run the engine from login, always
    eki serve                            run the engine in the foreground
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import httpx

from . import agent
from . import config as config_mod
from . import migrate
from .engine import Engine
from .runs import TERMINAL
from .store import Store

DEFAULT_SERVICE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent


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


def call(method: str, path: str, service: str, **kw) -> Any:
    ensure_engine(service)
    try:
        r = httpx.request(method, service + path, timeout=30, **kw)
    except httpx.HTTPError as e:
        print(f"! lost the engine: {e}", file=sys.stderr)
        raise SystemExit(1)
    if r.status_code >= 400 and r.status_code != 409:
        print(f"! {r.status_code} {r.text[:200]}", file=sys.stderr)
        raise SystemExit(1)
    return r.json()


# ---- asking ----------------------------------------------------------

def cmd_ask(cfg, args) -> int:
    conversation = ""
    if args.continue_:
        conversation = Store(cfg.db).latest_conversation() or ""
    body = {"prompt": args.prompt, "conversation": conversation,
            "backend": args.backend or "", "repo": args.repo or "",
            "images": bool(args.image)}
    started = call("POST", "/api/ask", args.service, json=body)
    if args.detach:
        print(started["run"])
        return 0
    return watch(started["run"], args.service, quiet=args.quiet,
                 conversation=started["conversation"])


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


def cmd_runs(args) -> int:
    rows = call("GET", f"/api/runs?limit={args.limit}", args.service)
    if not rows:
        print("nothing has run yet")
        return 0
    for r in rows:
        prompt = (r["prompt"] or "").replace("\n", " ")[:44]
        backend = r["backend"] or "-"
        folder = " ⌂" if r.get("cwd") else "  "
        print(f"{r['id']}  {r['state']:<11} {backend:<7}{folder} {prompt}")
    return 0


def cmd_cancel(args) -> int:
    out = call("POST", f"/api/runs/{args.id}/cancel", args.service)
    print("cancelled" if out.get("cancelled") else "not running")
    return 0 if out.get("cancelled") else 1


def cmd_diff(args) -> int:
    out = call("GET", f"/api/runs/{args.id}/diff", args.service)
    print(out["diff"] or "(no changes)")
    return 0


# ---- reading (straight from the database, no engine needed) ----------

async def cmd_backends(cfg) -> int:
    eng = Engine(cfg)
    for b in await eng.describe():
        caps = b["capabilities"]
        flags = ",".join(n for n, on in (
            ("repo", caps.get("repo")), ("tools", caps.get("tools")),
            ("vision", caps.get("vision")), ("images", caps.get("images_out")),
        ) if on) or "chat"
        mark = "ok  " if b["ok"] else "down"
        print(f"{mark} {b['key']:10} tier {b['tier']:<4} {flags:22} {b['detail']}")
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


def cmd_policy(cfg, args) -> int:
    """Routing preferences, edited in place — no config file surgery."""
    from . import policy as policy_mod
    current = policy_mod.load()
    known = {b.key for b in cfg.backends}

    if args.action == "show":
        print(f"disabled: {', '.join(current.disabled) or '(none)'}")
        print(f"tiers:    {current.tiers or '(as configured)'}")
        print(f"order:    {', '.join(current.order) or '(config order)'}")
        ceiling = ("(as configured)" if current.quota_ceiling is None
                   else current.quota_ceiling)
        print(f"ceiling:  {ceiling}")
        return 0

    if args.key and args.key not in known:
        print(f"no backend named {args.key!r} — known: {', '.join(sorted(known))}",
              file=sys.stderr)
        return 1

    if args.action == "off" and args.key not in current.disabled:
        current.disabled.append(args.key)
    elif args.action == "on":
        current.disabled = [k for k in current.disabled if k != args.key]
    elif args.action == "tier":
        if args.value is None:
            print("tier needs a number", file=sys.stderr)
            return 1
        current.tiers[args.key] = int(args.value)
    elif args.action == "prefer":
        current.order = [args.key] + [k for k in current.order if k != args.key]
    elif args.action == "ceiling":
        current.quota_ceiling = float(args.value) if args.value is not None else None

    path = policy_mod.save(current)
    print(f"saved {path}")
    # the running engine holds its own copy; tell it, if it's there
    if engine_up(args.service):
        httpx.put(f"{args.service}/api/policy", json=current.to_json(), timeout=5)
    return cmd_policy(cfg, argparse.Namespace(action="show", key="", value=None,
                                              service=args.service))


def cmd_models(cfg, args) -> int:
    """Local weights, without needing the service up."""
    from .models import ModelManager
    manager = ModelManager(cfg.local_models, cfg.memory_ceiling_gb)

    if args.action in ("start", "stop"):
        if manager.get(args.key) is None:
            print(f"no such model: {args.key}", file=sys.stderr)
            return 1
        coro = (manager.start(args.key, force=args.force) if args.action == "start"
                else manager.stop(args.key))
        print(asyncio.run(coro))
        return 0

    memory = manager.memory()
    for m in manager.describe():
        mark = "up  " if m["running"] else ("--  " if m["can_start"] else "    ")
        blocked = "  (no room)" if m["blocked_by_memory"] else ""
        print(f"{mark} {m['key']:8} :{m['port']:<6} {m['gb']:>5.1f}GB  "
              f"{m['label']}{blocked}")
    print(f"\n{memory.committed_gb}GB loaded · {memory.free_gb}GB free "
          f"of a {memory.ceiling_gb}GB ceiling ({memory.total_gb}GB installed)")
    return 0


def cmd_agent(args) -> int:
    if args.action == "install":
        # a hand-started engine holds the port; the agent's would fail to bind
        subprocess.run(["pkill", "-f", "eki.cli serve"], capture_output=True)
        time.sleep(0.5)
        print(agent.install(ROOT))
    elif args.action == "uninstall":
        print(agent.uninstall())
    elif args.action == "restart":
        print(agent.restart())
    else:
        print(agent.status())
    return 0


# ---- the parser ------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    migrate.run(Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(prog="eki", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=str(config_mod.default_path()))
    ap.add_argument("--service", default=DEFAULT_SERVICE, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="tell it to do something")
    a.add_argument("prompt")
    a.add_argument("--backend", help="force a backend by key")
    a.add_argument("-r", "--repo", help="a folder it may edit")
    a.add_argument("--image", action="store_true",
                   help="the answer is a picture — only image backends apply")
    a.add_argument("--continue", dest="continue_", action="store_true",
                   help="continue the most recent conversation")
    a.add_argument("-d", "--detach", action="store_true",
                   help="start it and print the run id instead of watching")
    a.add_argument("-q", "--quiet", action="store_true", help="answer only, at the end")

    r = sub.add_parser("runs", help="what's running and what finished")
    r.add_argument("-n", "--limit", type=int, default=30)
    for name, helptext in (("watch", "follow a run"), ("cancel", "stop a run"),
                           ("diff", "what a run changed")):
        sub.add_parser(name, help=helptext).add_argument("id")

    sub.add_parser("backends", help="list backends and health")
    h = sub.add_parser("history", help="recent conversations")
    h.add_argument("-n", "--limit", type=int, default=20)
    h.add_argument("-q", "--query", default="", help="only ones containing this text")
    sub.add_parser("show", help="print one conversation").add_argument("id")
    sub.add_parser("cost", help="what a conversation spent").add_argument("id")

    p = sub.add_parser("policy", help="routing preferences")
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "on", "off", "tier", "prefer", "ceiling"])
    p.add_argument("key", nargs="?", default="")
    p.add_argument("value", nargs="?", default=None)

    m = sub.add_parser("models", help="local weights: what's loaded, start, stop")
    m.add_argument("action", nargs="?", default="list", choices=["list", "start", "stop"])
    m.add_argument("key", nargs="?", default="")
    m.add_argument("-f", "--force", action="store_true",
                   help="start it even if the memory ceiling says no")

    g = sub.add_parser("agent", help="start the engine at login")
    g.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "restart", "status"])

    sv = sub.add_parser("serve", help="run the engine in the foreground")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)

    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .service import main as serve_main
        return serve_main(["-c", args.config, "--host", args.host,
                           "--port", str(args.port)])
    if args.cmd == "agent":
        return cmd_agent(args)
    if args.cmd == "runs":
        return cmd_runs(args)
    if args.cmd == "watch":
        return watch(args.id, args.service)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    if args.cmd == "diff":
        return cmd_diff(args)

    cfg = config_mod.load(args.config)
    if args.cmd == "ask":
        return cmd_ask(cfg, args)
    if args.cmd == "backends":
        return asyncio.run(cmd_backends(cfg))
    if args.cmd == "history":
        return cmd_history(cfg, args.limit, args.query)
    if args.cmd == "show":
        return cmd_show(cfg, args.id)
    if args.cmd == "cost":
        return cmd_cost(cfg, args.id)
    if args.cmd == "models":
        return cmd_models(cfg, args)
    if args.cmd == "policy":
        return cmd_policy(cfg, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
