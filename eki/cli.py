# SPDX-License-Identifier: Apache-2.0
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

    eki self "change yourself so…"       eki works on its own source: a branch,
                                         a diff and a verdict — never a merge
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
    else:
        print(agent.status())
    return 0


def cmd_self(args) -> int:
    """eki, working on eki (see eki/selfwork.py). Proposes; never merges."""
    from . import selfwork
    if not args.request:
        rows = selfwork.history()
        if not rows:
            print("nothing proposed yet — try: eki self \"…\"")
        for e in rows[-args.limit:]:
            when = time.strftime("%m-%d %H:%M", time.localtime(e.get("at") or 0))
            mark = "fit " if e.get("fit") else "    "
            print(f"{when}  {mark} self/{e['id']}  {e['request'][:60]}  — {e['verdict'][:70]}")
        return 0
    ensure_engine(args.service)
    say = lambda line: print(f"· {line}", file=sys.stderr, flush=True)   # noqa: E731
    try:
        from . import builds
        p = selfwork.propose(args.request, root=builds.source(), base=args.base,
                             check_base=not args.anyway,
                             ask=selfwork.ask_engine(args.service, args.backend or "", say),
                             say=say)
    except selfwork.SelfWorkError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    print(json.dumps(p.to_json(), indent=2) if args.json else "\n".join(p.lines()))
    from . import settings as settings_mod
    apply = args.apply or settings_mod.load().get("self_autonomy") == "apply"
    if apply and p.fit and not p.protected and p.commit:
        # apply here: the proposal's commit becomes a build, and the
        # supervisor swaps it in, watches it, and rolls back if it's unhealthy
        from . import builds
        build = builds.make(builds.source(), p.commit, note=f"self/{p.id}")
        builds.swap(build, self_id=p.id)
        print(f"\napplying: {build} — the supervisor swaps it in once runs finish, "
              f"watches it, and swaps back if it isn't healthy (eki builds)")
    elif apply and p.protected:
        print("\nnot applied: it touches what eki may not change alone — for a person to merge")
    return 0 if p.fit else 1


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
    print(f"swapping to {target} — once runs finish; watched {args.watch}s, rolled back if "
          f"unhealthy. Follow it: tail -f {got['log']}")
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

    k = sub.add_parser("skills", help="one set of skills for Claude Code, Codex and local models")
    k.add_argument("action", nargs="?", default="list",
                   choices=["list", "show", "cat", "new", "edit", "on", "off", "rm",
                            "import", "sync", "log", "learned", "learn"])
    k.add_argument("name", nargs="?", default="")
    k.add_argument("-d", "--description", default="", help="new: when a model should use it")
    k.add_argument("-f", "--file", default="", help="new: a SKILL.md to take as is")
    k.add_argument("--for", dest="backend", default="",
                   help="on/off: only for claude, codex or local")
    k.add_argument("--backends", default="", help="new: comma list (default all)")

    g = sub.add_parser("agent", help="start the engine at login")
    g.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "restart", "status"])

    sw = sub.add_parser("self", help="have eki change its own source, as a proposal")
    sw.add_argument("request", nargs="?", default="")
    sw.add_argument("-b", "--backend", help="the agent to use (default: routed)")
    sw.add_argument("--base", default="HEAD", help="commit or branch to start from")
    sw.add_argument("--anyway", action="store_true",
                    help="go ahead even if the base fails its own tests")
    sw.add_argument("--limit", type=int, default=20)
    sw.add_argument("--apply", action="store_true",
                    help="if it's fit and touches nothing protected, swap it in (watched, rolled back if unhealthy)")
    sw.add_argument("--json", action="store_true")

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
    sp.add_argument("--wait", type=int, default=600, help="seconds to wait for runs to finish")
    sp.add_argument("--watch", type=int, default=180, help="seconds it must stay healthy")

    sv = sub.add_parser("serve", help="run the engine in the foreground")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sub.add_parser("mcp", help="serve eki's tools over stdio (MCP) for Codex and other clients")

    args = ap.parse_args(argv)

    if args.cmd == "mcp":
        from . import mcpbridge
        depth = int(os.environ.get("EKI_DEPTH", "0") or 0)
        from . import settings as settings_mod
        bridge = mcpbridge.RemoteBridge(mcpbridge.RemoteEngine(args.service), depth=depth + 1,
                                        screen=bool(settings_mod.load().get("claude_screen", True)))
        asyncio.run(mcpbridge.serve_stdio(bridge))
        return 0
    if args.cmd == "serve":
        from .service import main as serve_main
        return serve_main(["-c", args.config, "--host", args.host,
                           "--port", str(args.port)])
    if args.cmd == "skills":
        return cmd_skills(args)
    if args.cmd == "builds":
        return cmd_builds(args)
    if args.cmd == "observe":
        return cmd_observe(args)
    if args.cmd == "routing":
        return cmd_routing(args)
    if args.cmd == "lineup":
        return cmd_watch(args)
    if args.cmd == "swap":
        return cmd_swap(args)
    if args.cmd == "agent":
        return cmd_agent(args)
    if args.cmd == "self":
        return cmd_self(args)
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
