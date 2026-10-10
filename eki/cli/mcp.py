"""One MCP registry for Claude Code and Codex."""
from __future__ import annotations

from .. import mcp

NAME = "mcp"
HELP = "list, add, remove or switch on/off MCP servers (shared by every program)"


def add(p) -> None:
    p.add_argument("action", choices=["list", "add", "remove", "on", "off"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--url", help="a remote (HTTP) server")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE, repeatable")
    p.add_argument("command", nargs="*", help="the command that starts a local server")


def run(args) -> int:
    if args.action != "list" and not args.name:
        print(f"eki mcp {args.action} needs a server name")
        return 1
    if args.action == "add":
        env = dict(e.split("=", 1) for e in args.env)
        cmd = list(args.command)
        try:
            mcp.add(args.name, command=cmd[0] if cmd else None, args=cmd[1:], url=args.url, env=env)
        except ValueError as e:
            print(e)
            return 1
        print(f"added {args.name}")
    elif args.action == "remove":
        print("removed" if mcp.remove(args.name) else "no such server")
    elif args.action in ("on", "off"):
        try:
            mcp.set_on(args.name, args.action == "on")
        except KeyError:
            print("no such server")
            return 1
        print(f"{args.name} {args.action}")
    else:
        rows = mcp.all_servers()
        for r in rows:
            spec = mcp.public(r["name"], r["spec"])
            what = spec.get("url") or " ".join([spec.get("command", "")] + spec.get("args", []))
            env = " ".join(f"{k}={v}" for k, v in (spec.get("env") or {}).items())
            print(f"{r['name']:<16} {'on ' if r['on'] else 'off'}  {what}" + (f"  {env}" if env else ""))
        if not rows:
            print("no servers yet — eki mcp add <name> -- <command …>  or  --url <url>")
    return 0
