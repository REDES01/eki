"""One MCP registry for every program.

`EKI_HOME/mcp.json` is in Claude Code's own format, `{"mcpServers": {…}}`,
so Claude gets the file itself (`--mcp-config`) and Codex gets the same
servers as `-c mcp_servers.…` overrides. Nothing is written into either
program's own config.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import paths
from .providers.codex import mcp_flags


def servers() -> Dict[str, Dict[str, Any]]:
    path = paths.config("mcp")
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()).get("mcpServers") or {})
    except ValueError:
        return {}


def save(all_servers: Dict[str, Dict[str, Any]]) -> None:
    paths.config("mcp").write_text(json.dumps({"mcpServers": all_servers}, indent=2) + "\n")


def add(name: str, *, command: Optional[str] = None, args: Optional[List[str]] = None,
        url: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> None:
    if not (command or url):
        raise ValueError("an MCP server needs a command or a url")
    spec: Dict[str, Any] = {"type": "http", "url": url} if url else {"command": command, "args": args or []}
    if env:
        spec["env"] = env
    current = servers()
    current[name] = spec
    save(current)


def remove(name: str) -> bool:
    current = servers()
    if current.pop(name, None) is None:
        return False
    save(current)
    return True


def claude_config() -> Optional[str]:
    return str(paths.config("mcp")) if servers() else None


def codex_config() -> List[str]:
    return mcp_flags(servers())
