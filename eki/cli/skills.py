# SPDX-License-Identifier: Apache-2.0
"""`eki skills`: one set of skills for Claude Code, Codex and local models."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .common import call

ORDER = 100


def register(sub) -> None:
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
    k.set_defaults(func=cmd_skills)


def cmd_skills(args) -> int:
    """The skill store, without needing the service up (eki/skills.py)."""
    from .. import skills
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
