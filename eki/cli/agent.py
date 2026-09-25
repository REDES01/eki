# SPDX-License-Identifier: Apache-2.0
"""`eki agent`: start the engine at login."""
from __future__ import annotations

import subprocess
import sys
import time

from .. import agent

ORDER = 120


def register(sub) -> None:
    g = sub.add_parser("agent", help="start the engine at login")
    g.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "restart", "status", "access"])
    g.set_defaults(func=cmd_agent)


def cmd_agent(args) -> int:
    if args.action == "install":
        # a hand-started engine holds the port; the agent's would fail to bind
        subprocess.run(["pkill", "-f", "eki.cli serve"], capture_output=True)
        time.sleep(0.5)
        from .. import builds
        print(agent.install(builds.source()))
    elif args.action == "uninstall":
        print(agent.uninstall())
    elif args.action == "restart":
        print(agent.restart())
    elif args.action == "access":
        from .. import launcher
        if launcher.request_access():
            print("macOS will ask for Screen Recording and Accessibility for “eki” — switch it on in "
                  "both, then `eki agent restart`")
        else:
            print("the eki app isn't built — `eki agent install` builds it", file=sys.stderr)
            return 1
    else:
        print(agent.status())
    return 0
