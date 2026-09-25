# SPDX-License-Identifier: Apache-2.0
"""`eki context`: one AGENTS.md for Claude Code and Codex."""
from __future__ import annotations

import json
import os
import subprocess
import sys

ORDER = 110


def register(sub) -> None:
    cx = sub.add_parser("context", help="one AGENTS.md for Claude Code and Codex")
    cx.add_argument("action", nargs="?", default="status",
                    choices=["status", "show", "edit", "import", "sync", "project", "use"])
    cx.add_argument("folder", nargs="?", default="",
                    help="project/use: the folder (default here); use also adds eki's section "
                         "and lets Claude Code run eki there")
    cx.add_argument("--claude", action="store_true", help="show/edit: the Claude-only part")
    cx.set_defaults(func=cmd_context)


def cmd_context(args) -> int:
    """The standing context both CLIs read, without the service (eki/standing.py)."""
    from .. import standing
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
