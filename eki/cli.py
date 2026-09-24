# SPDX-License-Identifier: Apache-2.0
"""eki — tell it to do something; it keeps doing it until it's done.

    eki ask "why is this slow?"          routed; the answer streams here
    eki ask "..." --backend claude       force a backend
    eki ask "..." --repo .               may edit files there
    eki ask "a brass compass" --image    draw it
    eki ask "..." --continue             same thread as last time
    eki ask "..." --detach               start it and return straight away

For an agent with a shell — blocking, quiet, a path out, real exit codes:

    eki capabilities                     what this Mac can do right now
    eki image "a brass compass" -o art/  a picture, saved; its path printed
    eki write "a sea shanty" -m qwen     text from that model into a file
    eki submit "…" [-m key] [--image]    start it, print its run id, return
    eki wait <run> [-o path]             block until it ends; its path or text
    ... --json                           one JSON object instead

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

    eki self "change yourself so…"       eki works on its own source: a branch,
                                         a diff and a verdict — never a merge
    eki self -r "…" -r "…"               several changes, queued; side by side as room allows
    eki self                             what it has proposed so far
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
from typing import Any, Dict, List, Optional

import httpx

from . import agent
from . import config as config_mod
from . import grant as grant_mod
from . import migrate
from .engine import Engine
from .runs import TERMINAL
from .store import Store

DEFAULT_SERVICE = "http://127.0.0.1:8787"
ROOT = Path(__file__).resolve().parent.parent
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


# ---- asking ----------------------------------------------------------

def cmd_ask(cfg, args) -> int:
    conversation = ""
    if args.continue_:
        conversation = Store(cfg.db).latest_conversation() or ""
    body = {"prompt": args.prompt, "conversation": conversation,
            "backend": args.backend or "", "repo": args.repo or "",
            "images": bool(args.image)}
    # run by a program the engine started (a goal's agent testing eki, say):
    # its request, not yours, one level down from the run asking
    from . import produce
    produce.from_agent(body, read_only=bool(getattr(args, "read_only", False)),
                       commands=getattr(args, "allow", None), paths=getattr(args, "write", None))
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


def cmd_allow(args) -> int:
    """Allow what a narrowed run was refused, and run it again; followed
    like an ask. From inside a narrowed run, no more than it has."""
    body = {"parent": grant_mod.from_env().to_json()} if os.environ.get(grant_mod.ENV) else {}
    out = call("POST", f"/api/runs/{args.id}/allow", args.service, json=body)
    if not out.get("run"):
        print("that run wasn't refused anything, or it was allowed already", file=sys.stderr)
        return 1
    return watch(out["run"], args.service)


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
        httpx.put(f"{args.service}/api/policy", json=current.to_json(), timeout=5,
                  headers=parent_headers())
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


def cmd_skills(args) -> int:
    """The skill store, without needing the service up (eki/skills.py)."""
    from . import skills
    act, name = args.action, args.name
    try:
        if act == "list":
            skills.boot()
            rows = skills.list_skills()
            for s in rows:
                on = ",".join(s["backends"]) if s["enabled"] else "off"
                bad = [b for b, v in s["views"].items() if v == "conflict"]
                warn = f"  (name taken in {', '.join(bad)})" if bad else ""
                print(f"{s['name']:24} [{on}]{warn}\n    {s['description'][:110]}")
            loose = [u for u in skills.unmanaged() if not u["held"]]
            if loose:
                print(f"\n{len(loose)} skill(s) in the CLIs' own folders; `eki skills import` takes them in:")
                for u in loose:
                    print(f"  {u['backend']:7} {u['path']}")
            if not rows and not loose:
                print(f"no skills yet — `eki skills new <name> -d \"when to use it\"` ({skills.STORE})")
            return 0
        if act in ("show", "cat"):
            print(skills.source(name), end="")
            return 0
        if act == "new":
            body = sys.stdin.read() if not sys.stdin.isatty() else f"# {name}\n\nInstructions go here.\n"
            if args.file:
                text = Path(args.file).expanduser().read_text()
                s = skills.put(name, text=text, description=args.description or "")
            else:
                s = skills.put(name, description=args.description or "", body=body,
                               backends=args.backends.split(",") if args.backends else None)
            print(s.get("path", ""))
            return 0
        if act == "edit":
            path = Path(skills.get(name)["path"]) / "SKILL.md" if skills.get(name) else None
            if path is None:
                raise KeyError(name)
            subprocess.run([os.environ.get("EDITOR", "vi"), str(path)])
            skills.put(name, text=path.read_text(), message=f"edit {name}")
            return 0
        if act in ("on", "off"):
            skills.set_enabled(name, act == "on", args.backend or "")
            return 0
        if act == "rm":
            skills.remove(name)
            return 0
        if act == "import":
            print(json.dumps(skills.import_existing([name] if name else None), indent=2))
            return 0
        if act == "sync":
            print(json.dumps(skills.sync(), indent=2))
            return 0
        if act == "learned":
            data = call("GET", "/api/skills-learned", args.service)
            rows = data.get("skills") or []
            for sk in rows:
                l = sk["learned"] or {}
                state = ("on" if sk["enabled"] else "off") + (", edited by you" if l.get("edited_by_you") else "")
                when = time.strftime("%Y-%m-%d", time.localtime(l.get("at") or 0))
                print(f"{sk['name']:24} [{state}] {when} ×{l.get('times', 1)}\n    {l.get('why', '')[:110]}")
            if not rows:
                print("eki hasn't learned a skill yet")
            revs = data.get("reviews") or []
            if revs:
                print("\nlatest reviews:")
                for r in revs[:10]:
                    when = time.strftime("%m-%d %H:%M", time.localtime(r.get("at") or 0))
                    sig = ",".join(r.get("signals") or [])
                    what = r.get("skill") or ""
                    print(f"  {when}  {r.get('result', ''):<10} {sig:<20} {what:<20} {(r.get('note') or '')[:60]}")
            return 0
        if act == "learn":
            if not name:
                print("eki skills learn <conversation-id>", file=sys.stderr)
                return 1
            print(json.dumps(call("POST", f"/api/conversations/{name}/learn", args.service), indent=2))
            return 0
        if act == "log":
            for h in skills.history(30, name):
                print(f"{h['commit']}  {h['date']}  {h['message']}")
            return 0
    except KeyError:
        print(f"no such skill: {name}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1


def cmd_context(args) -> int:
    """The standing context both CLIs read, without the service (eki/standing.py)."""
    from . import standing
    act = args.action
    try:
        if act == "status":
            report = standing.sync()
            for v in standing.state():
                note = "  (a file eki didn't make; `eki context import` takes it in)" \
                    if v["state"] == "conflict" else ""
                print(f"{v['backend']:7} {v['path']:40} {v['state']}{note}")
            print(f"\nsource: {standing.HOME}/AGENTS.md (everyone), CLAUDE.md (Claude only)")
            return 0 if not report.get("conflicts") else 1
        if act == "show":
            standing.ensure_source()
            name = "CLAUDE.md" if args.claude else "AGENTS.md"
            print((standing.HOME / name).read_text(), end="")
            return 0
        if act == "edit":
            standing.ensure_source()
            path = standing.HOME / ("CLAUDE.md" if args.claude else "AGENTS.md")
            subprocess.run([os.environ.get("EDITOR", "vi"), str(path)])
            standing.sync()
            return 0
        if act == "import":
            print(json.dumps(standing.import_existing(), indent=2))
            return 0
        if act == "sync":
            print(json.dumps(standing.sync(), indent=2))
            return 0
        if act in ("project", "use"):
            r = (standing.use_here if act == "use" else standing.project)(args.folder or ".")
            print("; ".join(r["done"]) or r.get("note") or "already so")
            return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1


def cmd_agent(args) -> int:
    if args.action == "install":
        # a hand-started engine holds the port; the agent's would fail to bind
        subprocess.run(["pkill", "-f", "eki.cli serve"], capture_output=True)
        time.sleep(0.5)
        from . import builds
        print(agent.install(builds.source()))
    elif args.action == "uninstall":
        print(agent.uninstall())
    elif args.action == "restart":
        print(agent.restart())
    elif args.action == "access":
        from . import launcher
        if launcher.request_access():
            print("macOS will ask for Screen Recording and Accessibility for “eki” — switch it on in "
                  "both, then `eki agent restart`")
        else:
            print("the eki app isn't built — `eki agent install` builds it", file=sys.stderr)
            return 1
    else:
        print(agent.status())
    return 0


SELF_VERBS = ("apply", "discard", "undo", "diff", "show", "on", "off", "next", "note",
              "autonomy", "retry", "drop", "mine", "parallel", "release")


def _ago(t: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t or 0))


def _self_status(service: str, limit: int) -> int:
    v = call("GET", "/api/self", service)
    if not v["can"]:
        print(v["why_not"])
        return 1
    g = v.get("goal")
    if g is None:
        print("eki works on itself: off — `eki self on` lets it, whenever the machine has room")
    else:
        spend = "your subscriptions' spare room" if g.get("spare") else "nothing but what you allow"
        if v.get("local"):
            spend += " and the local models"
        print(f"eki works on itself: {'on' if g['state'] == 'active' else g['state']} — uses {spend}"
              + ("   (eki self off)" if g["state"] == "active" else "   (eki self on)"))
        if g.get("note") and not g.get("working"):
            print(f"now: {g['note']}")
    going = _going_live(v.get("going_live") or {})
    if going:
        print(going)
    train = _next_go_live(v.get("train") or {})
    if train:
        print(train)
    areas = ", ".join(f"{k}: {m}" for k, m in (v.get("areas") or {}).items())
    print(f"autonomy: {v['autonomy']}" + (f" ({areas})" if areas else "")
          + f" · at most {v['review_max']} waiting for you · {v.get('parallel', 1)} at once")
    working = [i for i in v["working"] if i.get("phase") != "merging"]
    if working:
        print("\nworking on:")
        for i in working:
            where = ", ".join(i.get("areas") or []) or "?"
            step = i.get("step") or {}
            cut = step.get("state") == "interrupted" or (step and not step.get("live") and not i.get("live"))
            print(f"  {i['source']:<7} {i['title'][:60]}  [{where}]"
                  + ("" if i.get("live") else
                     f"  (cut off at {step.get('kind')} — carries on by itself)" if cut else
                     "  (carries on when there's room)"))
    if v.get("merging"):
        print("\nmerge queue (applied one at a time, in the order they finished):")
        for n, r in enumerate(v["merging"], 1):
            state = "applying now" if r.get("applying") else "waiting its turn" if r.get("live") \
                else "cut off — back in line when it carries on"
            print(f"  {n}. self/{r['change']}  {r.get('title', '')[:56]} — {state}")
    if v["waiting"]:
        print("\nwaiting for you:")
        for c in v["waiting"]:
            print(f"  self/{c['id']}  {c.get('source', ''):<7} {c['title'][:58]}"
                  f"\n              eki self diff {c['id']} · eki self apply {c['id']} · eki self discard {c['id']}")
    ahead = v["queue"]
    nexts = [f"  roadmap {n['section'].split(' — ')[0]}: {n['title'][:60]}" for n in v["roadmap"]["next"][:3]]
    if ahead or nexts:
        print("\nup next:")
        for i in ahead:
            print(f"  {i['source']:<7} {i['title'][:70]}")
        for line in nexts:
            print(line)
    if v["left"]:
        print("\nleft for you:")
        for i in v["left"]:
            print(f"  {i['id'][:6]}  {i['title'][:60]} — {i.get('note', '')[:90]}"
                  f"\n          (eki self retry {i['id'][:6]} to let it try again)")
    rows = [c for c in v["changes"] if c["state"] not in ("proposed", "conflicts")][:limit]
    if rows:
        print("\nrecent:")
        for c in rows:
            print(f"  {_ago(c.get('state_at'))}  {c['state']:<11} self/{c['id']}  {c['title'][:56]}")
    note = v.get("note")
    if note:
        print(f"\nweekly note ({note['id']}): {len(note.get('suggestions') or [])} suggestions — "
              "on the board (Goals → Self)")
    return 0


def _self_verb(args, verb: str, rest: List[str]) -> int:
    s = args.service
    arg = rest[0] if rest else ""
    if verb in ("on", "off"):
        got = call("POST", "/api/self/on", s, json={"on": verb == "on"})
        g = got.get("goal")
        print("eki works on itself whenever the machine has room — changes come to you as proposals "
              "(Goals → Self, or `eki self`)" if verb == "on" and g else
              "paused — nothing new starts; what's running finishes" if g else "it wasn't on")
        return 0
    if verb == "autonomy":
        if not rest:
            v = call("GET", "/api/self", s)
            print(f"{v['autonomy']}; areas: {json.dumps(v.get('areas') or {})}")
            return 0
        body: Dict[str, Any] = {}
        areas: Dict[str, str] = {}
        for word in rest:
            if "=" in word:
                path, _, mode = word.partition("=")
                areas[path] = mode
            else:
                body["autonomy"] = word
        if areas:
            v = call("GET", "/api/self", s)
            merged = {**(v.get("areas") or {}), **areas}
            body["areas"] = {k: m for k, m in merged.items() if m in ("apply", "propose")}
        print(json.dumps(call("PUT", "/api/self/settings", s, json=body)))
        return 0
    if verb == "parallel":
        if not arg:
            print(call("GET", "/api/self", s).get("parallel", 1))
            return 0
        got = call("PUT", "/api/self/settings", s, json={"parallel": int(arg)})
        print(f"at most {got['self_parallel']} at once — fewer when your subscriptions' spare room is short")
        return 0
    if verb == "release":
        if arg:
            got = call("PUT", "/api/self/settings", s, json={"release_minutes": int(arg)})
            print(f"applied changes go live together, at most once every {got['self_release_minutes']} min")
            return 0
        got = call("POST", "/api/self/release", s)
        print(f"going live now, carrying {', '.join('self/' + x for x in got.get('cars') or []) or 'what was waiting'}"
              if got.get("build") else got.get("why") or "nothing waiting to go live")
        return 0
    if verb == "next":
        v = call("GET", "/api/self", s)
        for i in v["working"] + v["queue"]:
            print(f"{i['source']:<8} {i['title']}")
        for n in v["roadmap"]["next"]:
            print(f"roadmap  {n['section']}: {n['title']}")
        return 0
    if verb == "note":
        v = call("GET", "/api/self", s)
        if arg == "now" or not v.get("note"):
            got = call("POST", "/api/self/note", s)
            print(f"writing this week's note: run {got.get('run')} — `eki watch {got.get('run')}`")
            return 0
        note = v["note"]
        print(f"eki's note of {note['id']}\n\n{note['text']}\n")
        for i, sug in enumerate(note.get("suggestions") or []):
            print(f"{i}. {sug['title']} [{sug['kind']}]{' — ' + sug['picked'] if sug.get('picked') else ''}"
                  f"\n   {sug['why']}")
        return 0
    if not arg:
        print(f"eki self {verb} <id>", file=sys.stderr)
        return 2
    if verb in ("retry", "drop", "mine"):
        got = call("POST", f"/api/self/items/{arg}/{'person' if verb == 'mine' else verb}", s)
        print(f"{got['title']}: {got['state']}")
        return 0
    if verb == "diff":
        print(call("GET", f"/api/self/changes/{arg}/diff", s)["diff"])
        return 0
    if verb == "show":
        print("\n".join(call("GET", f"/api/self/changes/{arg}", s)["lines"]))
        return 0
    body: Dict[str, Any] = {}
    if verb == "apply":
        if getattr(args, "now", False):
            body["now"] = True
        c = call("GET", f"/api/self/changes/{arg}", s)
        if c.get("protected"):
            if not _confirm_protected(c, getattr(args, "yes", False)):
                print("not applied", file=sys.stderr)
                return 1
            body["confirm"] = True
    if verb in ("apply", "undo"):
        print("· a change put on top of your checkout is judged again first — a minute or two",
              file=sys.stderr)
    try:
        # applying may rebase and re-run the checks: longer than an ordinary call
        r = httpx.post(f"{s}/api/self/changes/{arg}/{verb}", json=body or None, timeout=1800,
                       headers=parent_headers())
    except httpx.HTTPError as e:
        print(f"! lost the engine: {e}", file=sys.stderr)
        return 1
    got = r.json()
    if r.status_code >= 400:
        print(f"! {got.get('detail') or r.text[:200]}", file=sys.stderr)
        return 1
    say = {"applying": ("applying — it goes live " + ("now" if body.get("now") else
                        "with the next release train (`eki self` says when; `--now` doesn't wait)")
                        + ": the supervisor swaps it in (runs still going carry on in it), watches it, "
                        "and goes back if it isn't healthy (eki builds)"),
           "applied": got.get("merged") or "applied",
           "conflicts": _conflicts_said(got),
           "unfit": "it didn't pass its checks on top of your checkout",
           "discarded": "discarded", "not undone": f"not undone: {got.get('why')}"}
    print(say.get(got.get("state") or "", json.dumps(got)))
    return 0


def _conflicts_said(got: Dict[str, Any]) -> str:
    """An apply that conflicts usually isn't a failure: eki has the conflicts
    fixed in a run of its own and applies it after. Retry is only worth
    mentioning when no such run started (resolving is off)."""
    rid = got.get("resolving")
    if rid:
        return (f"it conflicts with your checkout, so eki is fixing the conflicts itself and "
                f"applies it when that's done — nothing for you to do (follow it: eki watch {rid})")
    return "it no longer goes on top of your checkout — `eki self retry` lets eki try again"


def _confirm_protected(c: Dict[str, Any], yes: bool) -> bool:
    """A change touching what eki may not change alone goes in only when you
    say so, having seen which files: a y/N prompt, or --yes."""
    print(f"self/{c['id']} touches what eki may not change alone:", file=sys.stderr)
    for f in c["protected"]:
        print(f"  {f}", file=sys.stderr)
    print(f"  (read it first: eki self diff {c['id']})", file=sys.stderr)
    if yes:
        return True
    if not sys.stdin.isatty():
        print("! not asked from a terminal — add --yes to apply it", file=sys.stderr)
        return False
    try:
        return input("apply it anyway? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _batch(args) -> List[str]:
    """The requests given with -r/--request and --batch (a file, `-` for
    standard input: one request per line, or paragraphs split by blank lines
    when any request runs over several lines)."""
    asked = [r.strip() for r in args.requests or [] if r.strip()]
    if args.batch:
        text = sys.stdin.read() if args.batch == "-" else Path(args.batch).expanduser().read_text()
        parts = [x for x in text.split("\n\n")] if "\n\n" in text.strip() else text.splitlines()
        asked += [" ".join(x.split()) for x in parts if x.strip() and not x.strip().startswith("#")]
    return asked


def _self_batch(args, asked: List[str]) -> int:
    """Several changes at once: each queued as its own item; they start as
    room allows, side by side where their areas don't overlap."""
    ensure_engine(args.service)
    queued, goal = 0, False
    for request in asked:
        got = call("POST", "/api/self", args.service,
                   json={"request": request, "when": "later", "apply": args.apply,
                         "backend": args.backend or ""})
        goal = goal or bool(got.get("goal"))
        queued += 1
        print(f"  queued: {request.splitlines()[0][:70]}")
    print(f"{queued} queued — they start as room allows (`eki self` shows them)" if goal else
          f"{queued} queued — but eki isn't working on itself yet: `eki self on`")
    return 0


def _intermixed(ap: argparse.ArgumentParser, args, extra: List[str]):
    """`eki self apply --yes abc` puts the id after a flag, where argparse
    stops filling `request` and leaves the rest over. For `eki self` those
    leftover words carry on the request, in order, so flags work anywhere;
    anything else left over (or an unknown flag) is still an error."""
    unknown = [w for w in extra if w.startswith("-") and w != "-"]
    if args.cmd != "self" or unknown:
        ap.error("unrecognized arguments: " + " ".join(unknown or extra))
    args.request = list(args.request or []) + extra
    return args


def cmd_self(args) -> int:
    """eki, working on eki (eki/selfwork.py, eki/selfloop.py, docs/self-build.md)."""
    words = list(args.request or [])
    asked = _batch(args)
    if asked:
        # several at once: the words given without a flag are one more
        return _self_batch(args, asked + ([" ".join(words).strip()] if words else []))
    verb = words[0].lower() if words else ""
    if verb in SELF_VERBS and (len(words) <= 2 or verb == "autonomy"):
        ensure_engine(args.service)
        return _self_verb(args, verb, words[1:])
    request = " ".join(words).strip()
    if not request:
        ensure_engine(args.service)
        return _self_status(args.service, args.limit)
    ensure_engine(args.service)
    body = {"request": request, "when": "later" if args.later else "now", "apply": args.apply,
            "base": args.base if args.base != "HEAD" else "", "check_base": not args.anyway,
            "backend": args.backend or ""}
    got = call("POST", "/api/self", args.service, json=body)
    if got.get("queued"):
        print("queued — eki takes it when the machine has room" if got.get("goal") else
              "queued — but eki isn't working on itself yet: `eki self on`")
        return 0
    print(f"· run {got['run']} — its own thread in the app; Ctrl-C stops watching, not the work",
          file=sys.stderr)
    watch(got["run"], args.service)
    item = call("GET", f"/api/self/items/{got['item']}", args.service)
    c = item.get("change_detail")
    if not c:
        print(f"\n{item['state']}: {item.get('note') or 'no change was made'}")
        return 1
    if args.json:
        print(json.dumps(c, indent=2))
    return 0 if c.get("fit") else 1


def cmd_routing(args) -> int:
    """The routing table, and checking it: explain a request, replay recent ones."""
    act, rest = args.action, " ".join(args.rest)
    if act == "explain":
        if not rest:
            print('eki routing explain "your request"', file=sys.stderr)
            return 1
        d = call("POST", "/api/routing/explain", args.service, json={"prompt": rest})
        lab = d["label"]
        print(f"prompt check : {lab['task']} · {lab['difficulty']} ({lab['source']}) → row “{d['row_title']}” — {d['row_why']}")
        print(f"that row     : {' → '.join(d['row_targets']) or '(empty)'}   [{d['row_source']}]")
        print(f"goes to      : {d['choice'] or 'nothing'} — {d['reason']}")
        for r in d.get("rejected") or []:
            print(f"   not {r}")
        return 0
    if act == "replay":
        rows = call("GET", f"/api/routing/replay?limit={int(rest or 40)}", args.service)
        diff = [r for r in rows if r["differs"]]
        for r in rows:
            mark = "≠" if r["differs"] else " "
            print(f"{mark} {r['then']:<14} → {r['now']:<28} {r['row']:<26} {r['prompt'][:60]}")
        print(f"\n{len(diff)} of {len(rows)} recent requests would go somewhere else now")
        return 0
    if act in ("undo", "forget"):
        got = call("POST", f"/api/routing/forget/{rest or 'all'}", args.service)
        print(("taken back: " + ", ".join(got["removed"])) if got["removed"] else "no rule like that")
        return 0
    print(call("GET", "/api/routing", args.service)["text"])
    return 0


def cmd_watch(args) -> int:
    """Which models are out there: the vendors' ladders, local suggestions."""
    if args.action == "refresh":
        print("reading the vendors' pages and Ollama's popular models… (a minute or two)",
              file=sys.stderr)
        httpx_timeout = 900
        try:
            data = httpx.post(f"{args.service}/api/watch/refresh", timeout=httpx_timeout).json()
        except httpx.HTTPError as e:
            print(f"! {e}", file=sys.stderr)
            return 1
    elif args.action == "update":
        from . import watch as watch_mod
        data = call("GET", "/api/watch", args.service)
        todo = [(k, v.get("program") or {}) for k, v in (data.get("vendors") or {}).items()
                if (v.get("program") or {}).get("behind") and (not args.name or args.name in k)]
        if not todo:
            print("the programs are up to date (as of the last reading)")
            return 0
        failed = False
        for key, prog in todo:
            print(f"updating {key} {prog.get('installed')} → {prog.get('latest')}: {' '.join(prog['update'])}")
            if subprocess.run(prog["update"]).returncode != 0:
                print(f"! {key} didn't update — see above", file=sys.stderr)
                failed = True
        if failed:
            return 1
        print("done — `eki lineup refresh` to read again; open threads pick the new version up "
              "when their session next starts")
        return 0
    elif args.action == "take":
        got = call("POST", f"/api/watch/take/{args.name}", args.service)
        print(f"setting up {args.name}: run {got.get('run')} — follow it with `eki watch {got.get('run')}`"
              if got.get("run") else json.dumps(got))
        return 0
    else:
        data = call("GET", "/api/watch", args.service)
    if not data.get("at"):
        print("not read yet — `eki lineup refresh`")
        return 0
    when = time.strftime("%m-%d %H:%M", time.localtime(data["at"]))
    print(f"read {when}")
    for prov, v in (data.get("vendors") or {}).items():
        lad = v.get("ladder") or {}
        roles = " · ".join(f"{r}: {lad[r] or '(its default)'}" for r in ("default", "top", "fast") if r in lad)
        print(f"\n{prov} — {v.get('vendor')}: {roles}")
        if v.get("guidance"):
            print(f"  “{v['guidance'][:220]}”")
        prog = v.get("program") or {}
        for st in prog.get("stale") or []:
            print(f"  ! its {st['role']} is {st['vendor']}, but {st['alias']!r} here runs {st['runs']}")
        if prog.get("behind"):
            print(f"  ! {prog.get('installed')} installed, {prog['latest']} is out — `eki lineup update`")
        if v.get("not_offered"):
            print(f"  listed by {v.get('vendor')} but not runnable here yet: {', '.join(v['not_offered'][:5])}")
    sugg = data.get("suggestions") or []
    print("\nlocal:" if sugg else "\nlocal: nothing better than what you run, today")
    for s in sugg:
        build = s.get("build") or "no MLX build yet"
        print(f"  {s['name']:<22} {s['why']}\n  {'':<22} → {build}   (eki lineup take {s['name']})")
    for e in data.get("errors") or []:
        print(f"  ! {e}")
    return 0


def cmd_observe(args) -> int:
    """What eki noticed about itself, and the fixes it proposed (eki/observe.py)."""
    from . import observe
    data = observe.summary(args.days)
    kinds = data["kinds"]
    if not kinds:
        print(f"nothing noticed in the last {args.days:g} days")
        return 0
    print(f"last {args.days:g} days: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    if data["faults"]:
        print("\nfaults in eki's own code:")
        for f in data["faults"]:
            p = f.get("proposal") or {}
            state = p.get("state") or "—"
            extra = f"  {p['branch']}" if p.get("branch") else ""
            print(f"  ×{f['count']:<3} {f['signature']}\n        {f.get('error', '')[:90]}"
                  f"\n        fix: {state}{extra}" + (f" — {p['verdict'][:80]}" if p.get("verdict") else ""))
    if args.all:
        print("\nlatest:")
        for e in data["recent"]:
            when = time.strftime("%m-%d %H:%M", time.localtime(e["at"]))
            what = e.get("signal") or e.get("what") or e.get("signature") or e.get("error") or ""
            print(f"  {when}  {e['kind']:<8} {str(what)[:60]:<60} {e.get('backend', '')}")
    return 0


def _next_go_live(t: Dict[str, Any]) -> str:
    """"next go-live in N min, carrying: …" — the release train (builds.board)."""
    if not t or not t.get("carrying"):
        return ""
    left = int(t.get("in") or 0)
    when = "now" if left <= 0 else f"in {max(1, -(-left // 60))} min"
    what = ", ".join(f"self/{c['id']} ({c.get('title', '')[:40]})" for c in t["carrying"][:6])
    more = f" and {len(t['carrying']) - 6} more" if len(t["carrying"]) > 6 else ""
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


def cmd_builds(args) -> int:
    """What the engine runs, what it ran before, and how the last swap went."""
    from . import builds
    rows = builds.listing()
    for r in rows:
        mark = "→" if r["current"] else ("↩" if r["previous"] else " ")
        what = "your checkout" if r.get("dev") else (r.get("note") or r.get("ref") or "")
        print(f"{mark} {r['id']:<11} {(r.get('commit') or '')[:10]:<11} {what:<24} {r['path']}")
    if not rows:
        print("no builds — the engine runs from your checkout (`eki agent install` sets this up)")
    s = builds.last_swap()
    if s:
        when = time.strftime("%m-%d %H:%M", time.localtime(s.get("at") or 0))
        print(f"\nlast swap {when}: {s.get('state')} — {s.get('target')}"
              + (f" ({s['why']})" if s.get("why") else ""))
    else:
        print("\nno swap yet")
    going = _going_live(builds.going_live())
    if going:
        print(going)
    return 0


def _when_of(every: str, at: str) -> Optional[Dict[str, Any]]:
    """--every day|weekdays|week|mon…sun|<n>h|<n>m, --at HH:MM → a goal's when."""
    if not every:
        return None
    every = every.lower().strip()
    at = at or "09:00"
    days = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    if every in ("day", "daily"):
        return {"kind": "daily", "at": at}
    if every in ("weekday", "weekdays"):
        return {"kind": "daily", "at": at, "days": [0, 1, 2, 3, 4]}
    if every in ("week", "weekly"):
        return {"kind": "daily", "at": at, "days": [0]}
    if every[:3] in days:
        return {"kind": "daily", "at": at, "days": [days[every[:3]]]}
    import re as _re
    m = _re.fullmatch(r"(\d+)\s*(h|m|hours?|min(utes?)?)", every)
    if m:
        n = int(m.group(1))
        return {"kind": "interval", "minutes": n * 60 if m.group(2).startswith("h") else n}
    raise SystemExit(f"--every is day, weekdays, week, a weekday (mon…sun), or like 4h / 90m — not {every!r}")


def _goal_line(g: Dict[str, Any]) -> str:
    folder = f" · {g['folder']}" if g.get("folder") else ""
    spare = " · may use subscriptions" if g.get("spare") else ""
    if g.get("screen"):
        spare = " · uses the screen when you're away, and subscriptions"
    if g.get("repeats") and g.get("on_time"):
        spare += " · on time"
    if g.get("repeats") and g.get("fresh"):
        spare += " · fresh each time"
    return f"{g['id'][:6]}  {g['status']:<10} {g['text'][:70]}\n        {g['when_text']}{folder}{spare}" + \
        (f"\n        {g['note']}" if g.get("note") else "")


def cmd_goals(args) -> int:
    """Goals: things eki keeps doing when the machine has room (eki/goals.py)."""
    a = args.action
    if a == "add":
        if not args.arg:
            print('eki goals add "every morning, look at my X and propose 3 posts" --every day --at 08:00',
                  file=sys.stderr)
            return 2
        folder = os.path.abspath(os.path.expanduser(args.folder)) if args.folder else ""
        g = call("POST", "/api/goals", args.service,
                 json={"text": args.arg, "when": _when_of(args.every, args.at), "folder": folder,
                       "spare": args.spare, "screen": args.screen, "on_time": args.on_time,
                       "fresh": args.fresh})
        print(f"added {g['id'][:6]} — {g['when_text']}; its first turn comes when the machine has room")
        return 0
    if a in ("rm", "pause", "resume", "run"):
        if not args.arg:
            print(f"eki goals {a} <goal id>", file=sys.stderr)
            return 2
        gid = next((g["id"] for g in call("GET", "/api/goals", args.service)["goals"]
                    if g["id"].startswith(args.arg)), args.arg)
        if a == "rm":
            got = call("DELETE", f"/api/goals/{gid}", args.service)
            print(f"removed {gid[:6]} (its thread stays in your history)")
        elif a == "run":
            call("POST", f"/api/goals/{gid}/run", args.service)
            print("its next turn comes as soon as there's room")
        else:
            call("PATCH", f"/api/goals/{gid}", args.service,
                 json={"state": "paused" if a == "pause" else "active"})
            print("paused" if a == "pause" else "resumed")
        return 0
    if a == "mode":
        body = {"on": True} if args.arg == "on" else {"on": False} if args.arg == "off" else \
            {"when": args.arg} if args.arg in ("resources", "away") else None
        if body is None:
            print("eki goals mode on|off          (background work at all)\n"
                  "eki goals mode resources|away  (whenever there's room, or only when you're away)",
                  file=sys.stderr)
            return 2
        call("POST", "/api/goals/mode", args.service, json=body)
    if a == "report":
        hours = float(args.arg or 24)
        r = call("GET", "/api/goals/report", args.service, params={"hours": hours})
        mins = round(r["working_seconds"] / 60)
        print(f"last {hours:g} h: {r['turns']} turns ({r['finished']} finished a goal), {r['failed']} failed, "
              f"{r['stepped_out']} stepped out for you · {mins} min of work")
        for key, b in r["by_backend"].items():
            print(f"  {key:<22} {int(b['turns'])} turns, {round(b['seconds'] / 60)} min")
        print(f"  on a subscription: {r['on_subscription']} turns")
        return 0
    v = call("GET", "/api/goals", args.service)
    sh = v["shift"]
    print(f"background: {'on' if v['on'] else 'off'} · "
          f"{'whenever there is room' if v['when'] == 'resources' else 'only when you are away'}")
    print(f"now: {sh.get('state')} — {sh.get('why')}")
    if not v["goals"]:
        print('no goals — eki goals add "…"   (or New goal on the board: http://127.0.0.1:8787/goals)')
    for g in v["goals"]:
        print(_goal_line(g))
    return 0


def cmd_swap(args) -> int:
    """Move the engine onto another build, through the supervisor."""
    from . import builds, candidate
    src = builds.source()
    why = builds.cant_change_code(src)
    if why:
        print(f"! {why}", file=sys.stderr)
        return 1
    if args.back:
        target = builds.BUILDS / "previous"
        if not target.is_symlink():
            print("nothing to go back to", file=sys.stderr)
            return 1
        target = target.resolve()
    elif args.dev:
        target = src
    else:
        # what the engine runs goes into your checkout first, if it can; a
        # build that would still drop it is refused (eki/builds.py, the line)
        caught = builds.catch_up(src)
        if caught:
            print(f"· {caught}", file=sys.stderr)
        lost = builds.behind(src, args.ref)
        if lost and not args.force:
            subject = builds._g(src, "log", "-1", "--format=%s", lost).stdout.strip()
            print(f"! the engine runs {lost[:10]} ({subject}), which {args.ref} doesn't have — "
                  f"swapping would drop it. Commit your edits so eki can bring it into your "
                  f"checkout, or merge {lost[:10]}; --force swaps anyway", file=sys.stderr)
            return 1
        try:
            target = builds.make(src, args.ref)
        except (ValueError, RuntimeError) as e:
            print(f"! {e}", file=sys.stderr)
            return 1
        if not args.no_check:
            say = lambda line: print(f"· {line}", file=sys.stderr, flush=True)   # noqa: E731
            report = candidate.check(target, python=sys.executable, say=say,
                                     skip=("tests",) if args.skip_tests else ())
            if not report.fit:
                print(f"! {target.name} isn't fit to run — not swapping", file=sys.stderr)
                return 1
    got = builds.swap(target, wait=args.wait, watch=args.watch)
    mins = max(0, got["deadline"] - int(time.time())) // 60
    print(f"swapping to {target} — at a quiet moment, or in {mins} min anyway (runs still going "
          f"carry on in the new engine); watched {args.watch}s, rolled back if unhealthy. "
          f"Follow it: tail -f {got['log']}")
    return 0


# ---- the parser ------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    migrate.run(Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(prog="eki", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=str(config_mod.default_path()))
    ap.add_argument("--service", default=DEFAULT_SERVICE, help=argparse.SUPPRESS)
    from . import __version__
    ap.add_argument("--version", action="version", version=f"eki {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="tell it to do something")
    a.add_argument("prompt")
    a.add_argument("--backend", help="force a backend by key")
    a.add_argument("-r", "--repo", help="a folder it may edit")
    a.add_argument("--image", action="store_true",
                   help="the answer is a picture — only image backends apply")
    a.add_argument("--read-only", action="store_true",
                   help="from an agent: the run may read, not edit (a review)")
    a.add_argument("--allow", action="append", metavar="CMD",
                   help="from an agent: a command the run may use, e.g. 'pytest' (repeatable)")
    a.add_argument("--write", action="append", metavar="PATH",
                   help="from an agent: a path the run may write besides its copy (repeatable)")
    a.add_argument("--continue", dest="continue_", action="store_true",
                   help="continue the most recent conversation")
    a.add_argument("-d", "--detach", action="store_true",
                   help="start it and print the run id instead of watching")
    a.add_argument("-q", "--quiet", action="store_true", help="answer only, at the end")

    for name, helptext in (("image", "make a picture, save it, print its path (for agents)"),
                           ("write", "have a model write something, save it, print its path (for agents)")):
        c = sub.add_parser(name, help=helptext)
        c.add_argument("prompt")
        c.add_argument("-m", "--model", default="", help="a backend by key (see `eki backends`); default routed")
        c.add_argument("-o", "--output", default="",
                       help="a file or folder (default: here)" + ("; - prints the text" if name == "write" else ""))
        c.add_argument("--json", action="store_true", help="one JSON object: ok, paths, run, backend, error")
        c.add_argument("--timeout", type=float, default=900, help="seconds to wait (exit 5 after)")
        if name == "image":
            c.add_argument("--width", type=int, default=0)
            c.add_argument("--height", type=int, default=0)
            c.add_argument("-n", "--count", type=int, default=0, help="how many (1-4)")

    su = sub.add_parser("submit", help="start something slow, print its run id, return (for agents)")
    su.add_argument("prompt")
    su.add_argument("-m", "--model", default="", help="a backend by key (see `eki backends`); default routed")
    su.add_argument("-r", "--repo", help="a folder it may edit")
    su.add_argument("--image", action="store_true", help="the answer is a picture")
    su.add_argument("--read-only", action="store_true", help="the run may read, not edit (a review)")
    su.add_argument("--allow", action="append", metavar="CMD", help="a command the run may use (repeatable)")
    su.add_argument("--write", action="append", metavar="PATH",
                    help="a path the run may write besides its copy (repeatable)")
    su.add_argument("--json", action="store_true", help="one JSON object: ok, run, conversation, error")
    wt = sub.add_parser("wait", help="wait for a submitted run; print what it made (for agents)")
    wt.add_argument("id")
    wt.add_argument("-o", "--output", default="",
                    help="pictures: a folder or file (default: here); text: a file (default: printed)")
    wt.add_argument("--json", action="store_true", help="one JSON object: ok, paths, text, run, backend, error")
    wt.add_argument("--timeout", type=float, default=3600, help="seconds to wait (exit 5 after; it keeps running)")

    cp = sub.add_parser("capabilities", help="what this Mac can do right now, and how to reach it (for agents)")
    cp.add_argument("--json", action="store_true",
                    help="one JSON object: ok, depth, max_depth, can_ask, backends, error")

    r = sub.add_parser("runs", help="what's running and what finished")
    r.add_argument("-n", "--limit", type=int, default=30)
    for name, helptext in (("watch", "follow a run"), ("cancel", "stop a run"),
                           ("diff", "what a run changed"),
                           ("allow", "allow what a run was refused, and run it again")):
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

    k = sub.add_parser("skills", help="one set of skills for Claude Code, Codex and local models")
    k.add_argument("action", nargs="?", default="list",
                   choices=["list", "show", "cat", "new", "edit", "on", "off", "rm",
                            "import", "sync", "log", "learned", "learn"])
    k.add_argument("name", nargs="?", default="")
    k.add_argument("-d", "--description", default="", help="new: when a model should use it")
    k.add_argument("-f", "--file", default="", help="new: a SKILL.md to take as is")
    k.add_argument("--for", dest="backend", default="",
                   help="on/off: only for claude, codex, gemini or local")
    k.add_argument("--backends", default="", help="new: comma list (default all)")

    cx = sub.add_parser("context", help="one AGENTS.md for Claude Code and Codex")
    cx.add_argument("action", nargs="?", default="status",
                    choices=["status", "show", "edit", "import", "sync", "project", "use"])
    cx.add_argument("folder", nargs="?", default="",
                    help="project/use: the folder (default here); use also adds eki's section "
                         "and lets Claude Code run eki there")
    cx.add_argument("--claude", action="store_true", help="show/edit: the Claude-only part")

    g = sub.add_parser("agent", help="start the engine at login")
    g.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "restart", "status", "access"])

    sw = sub.add_parser("self", help="eki working on itself: ask for a change, see what it did, decide",
                        description="eki self                      what it's doing, what waits for you, what's next\n"
                                    "eki self \"change …\"           a change to eki, now (--later: when there's room)\n"
                                    "eki self -r \"…\" -r \"…\"         several changes, queued; they start as room allows\n"
                                    "eki self --batch FILE         the same, one request per line (- reads stdin)\n"
                                    "eki self parallel [N]         the most self-work at once (default 2)\n"
                                    "eki self release [N]          go live now with what's applied; N: at most one go-live every N min (15)\n"
                                    "eki self on|off               let eki work on itself whenever the machine has room\n"
                                    "eki self diff|show|apply|discard|undo <id>\n"
                                    "eki self next                 what it would take next\n"
                                    "eki self retry|drop|mine <item>\n"
                                    "eki self autonomy propose|apply [path=apply …]\n"
                                    "eki self note [now]           the weekly note: what it noticed, what it suggests",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    sw.add_argument("request", nargs="*", default=[])
    sw.add_argument("--later", action="store_true",
                    help="queue it: eki takes it when the machine has room (eki self on)")
    sw.add_argument("-r", "--request", dest="requests", action="append", metavar="REQUEST",
                    help="one of several changes to queue at once (repeat it)")
    sw.add_argument("--batch", metavar="FILE",
                    help="queue a change per line of FILE (blank-line paragraphs for longer ones; - is stdin)")
    sw.add_argument("-b", "--backend", help="the agent to use (default: routed)")
    sw.add_argument("--base", default="HEAD", help="commit or branch to start from")
    sw.add_argument("--anyway", action="store_true",
                    help="go ahead even if the base fails its own tests")
    sw.add_argument("--limit", type=int, default=20)
    sw.add_argument("--apply", action="store_true",
                    help="if it's fit and touches nothing protected, swap it in (watched, rolled back if unhealthy)")
    sw.add_argument("--json", action="store_true")
    sw.add_argument("-y", "--yes", action="store_true",
                    help="eki self apply: don't ask before applying a change that touches protected paths")
    sw.add_argument("--now", action="store_true",
                    help="eki self apply: go live at once, not with the next release train")

    ro = sub.add_parser("routing", help="the routing table; explain a request; replay recent ones")
    ro.add_argument("action", nargs="?", default="show", choices=["show", "explain", "replay", "undo", "forget"])
    ro.add_argument("rest", nargs="*")
    wa = sub.add_parser("lineup", help="which models are out there: vendor ladders, local suggestions")
    wa.add_argument("action", nargs="?", default="show", choices=["show", "refresh", "take", "update"])
    wa.add_argument("name", nargs="?", default="")
    ob = sub.add_parser("observe", help="what eki noticed about itself, and the fixes it proposed")
    ob.add_argument("--days", type=float, default=7)
    ob.add_argument("--all", action="store_true", help="also the latest entries of every kind")
    sub.add_parser("builds", help="what the engine runs, what it ran before, the last swap")
    sp = sub.add_parser("swap", help="move the engine onto another build, watched, with a way back")
    sp.add_argument("ref", nargs="?", default="HEAD", help="a commit or branch in your checkout")
    sp.add_argument("--back", action="store_true", help="to the previous build")
    sp.add_argument("--dev", action="store_true", help="back to running your checkout itself")
    sp.add_argument("--no-check", action="store_true", help="skip the candidate check")
    sp.add_argument("--skip-tests", action="store_true", help="candidate check without the test suite")
    sp.add_argument("--force", action="store_true",
                    help="swap even if it drops something the engine runs now")
    sp.add_argument("--wait", type=int, default=120,
                    help="seconds to wait for a moment with no runs before swapping anyway")
    sp.add_argument("--watch", type=int, default=180, help="seconds it must stay healthy")

    g = sub.add_parser("goals", help="things eki keeps doing when the machine has room")
    g.add_argument("action", nargs="?", default="show",
                   choices=["show", "add", "rm", "pause", "resume", "run", "mode", "report"])
    g.add_argument("arg", nargs="?", default="",
                   help="what to keep doing, in words (add); a goal id (rm, pause, resume, run); "
                        "on|off|resources|away (mode); hours (report)")
    g.add_argument("-f", "--folder", default="", help="add: a folder for it to work in")
    g.add_argument("--every", default="", help="add: day, weekdays, week, mon…sun, or like 4h (default: once, until done)")
    g.add_argument("--at", default="", help="add: the time of day, HH:MM (default 09:00)")
    g.add_argument("--spare", action="store_true",
                   help="add: may use your subscriptions, within their spare room")
    g.add_argument("--screen", action="store_true",
                   help="add: may look at and use the screen — its turns run only while you're away "
                        "(and it may use your subscriptions' spare room)")
    g.add_argument("--on-time", action="store_true",
                   help="add, repeating: run at the time, not when the machine has room")
    g.add_argument("--fresh", action="store_true",
                   help="add, repeating: each time in a new thread, not carrying on the last")

    sv = sub.add_parser("serve", help="run the engine in the foreground")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sub.add_parser("mcp", help="serve eki's tools over stdio (MCP) for Codex and other clients")

    args, extra = ap.parse_known_args(argv)
    if extra:
        args = _intermixed(ap, args, extra)

    if args.cmd == "mcp":
        from . import mcpbridge
        from . import nesting
        # started by an agent eki runs: its environment already says how deep
        depth, parent = nesting.caller()
        from . import settings as settings_mod
        # EKI_SCREEN=0: started for a thread that mustn't use the screen (a goal's)
        screen = bool(settings_mod.load().get("claude_screen", True)) and os.environ.get("EKI_SCREEN") != "0"
        # EKI_PARENT: the thread whose program started this server (Codex's)
        bridge = mcpbridge.RemoteBridge(mcpbridge.RemoteEngine(args.service), depth=depth,
                                        screen=screen, parent=parent,
                                        conversation=os.environ.get("EKI_PARENT", ""))
        asyncio.run(mcpbridge.serve_stdio(bridge))
        return 0
    if args.cmd == "serve":
        from .service import main as serve_main
        return serve_main(["-c", args.config, "--host", args.host,
                           "--port", str(args.port)])
    if args.cmd == "skills":
        return cmd_skills(args)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "builds":
        return cmd_builds(args)
    if args.cmd == "observe":
        return cmd_observe(args)
    if args.cmd == "routing":
        return cmd_routing(args)
    if args.cmd == "lineup":
        return cmd_watch(args)
    if args.cmd == "goals":
        return cmd_goals(args)
    if args.cmd == "swap":
        return cmd_swap(args)
    if args.cmd == "agent":
        return cmd_agent(args)
    if args.cmd == "self":
        return cmd_self(args)
    if args.cmd in ("image", "write", "capabilities", "submit", "wait"):
        from . import produce
        return getattr(produce, args.cmd)(args, ensure_engine)
    if args.cmd == "runs":
        return cmd_runs(args)
    if args.cmd == "watch":
        return watch(args.id, args.service)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    if args.cmd == "diff":
        return cmd_diff(args)
    if args.cmd == "allow":
        return cmd_allow(args)

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
