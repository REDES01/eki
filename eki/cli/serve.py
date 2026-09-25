# SPDX-License-Identifier: Apache-2.0
"""`eki serve` / `eki mcp`: the engine in the foreground, and eki's tools over stdio."""
from __future__ import annotations

import asyncio
import os

ORDER = 200


def register(sub) -> None:
    sv = sub.add_parser("serve", help="run the engine in the foreground")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.set_defaults(func=cmd_serve)
    sub.add_parser("mcp", help="serve eki's tools over stdio (MCP) for Codex and other clients").set_defaults(
        func=cmd_mcp)


def cmd_serve(args) -> int:
    from ..service import main as serve_main
    return serve_main(["-c", args.config, "--host", args.host,
                       "--port", str(args.port)])


def cmd_mcp(args) -> int:
    from .. import mcpbridge
    from .. import nesting
    # started by an agent eki runs: its environment already says how deep
    depth, parent = nesting.caller()
    from .. import settings as settings_mod
    # EKI_SCREEN=0: started for a thread that mustn't use the screen (a goal's)
    screen = bool(settings_mod.load().get("claude_screen", True)) and os.environ.get("EKI_SCREEN") != "0"
    # EKI_PARENT: the thread whose program started this server (Codex's)
    bridge = mcpbridge.RemoteBridge(mcpbridge.RemoteEngine(args.service), depth=depth,
                                    screen=screen, parent=parent,
                                    conversation=os.environ.get("EKI_PARENT", ""),
                                    folder=os.getcwd())
    asyncio.run(mcpbridge.serve_stdio(bridge))
    return 0
