"""One MCP registry for Claude Code and Codex."""
from __future__ import annotations

from .. import mcp

NAME = "mcp"
HELP = "list, add or remove MCP servers (shared by every program)"


def add(p) -> None:
    p.add_argument("action", choices=["list", "add", "remove"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--url", help="a remote (HTTP) server")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE, repeatable")
    p.add_argument("command", nargs="*", help="the command that starts a local server")


def run(args) -> int:
    if args.action == "add":
        env = dict(e.split("=", 1) for e in args.env)
        cmd = list(args.command)
        mcp.add(args.name, command=cmd[0] if cmd else None, args=cmd[1:], url=args.url, env=env)
        print(f"added {args.name}")
    elif args.action == "remove":
        print("removed" if mcp.remove(args.name) else "no such server")
    else:
        servers = mcp.servers()
        for name, spec in servers.items():
            print(f"{name:<16} {spec.get('url') or ' '.join([spec.get('command', '')] + spec.get('args', []))}")
        if not servers:
            print("no servers yet — eki mcp add <name> -- <command …>  or  --url <url>")
    return 0
