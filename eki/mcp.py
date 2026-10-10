"""One MCP registry for every program.

`EKI_HOME/mcp.json` is in Claude Code's own format, `{"mcpServers": {…}}`,
so Claude gets the file itself (`--mcp-config`) and Codex gets the same
servers as `-c mcp_servers.…` overrides. Nothing is written into either
program's own config.

A server switched off moves to `mcp-off.json`, same format, so `mcp.json`
holds only the servers that are on and Claude can read it as is. Env
values (keys, tokens the person typed) never leave this module whole:
`public()` shows each as `••••` plus its last two characters.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from . import paths
from .providers.codex import mcp_flags

#: which provider kinds run each program that takes MCP servers
PROGRAM_KINDS = {"claude": ("claude_code",), "codex": ("codex", "codex_local")}


def _read(file: str) -> Dict[str, Dict[str, Any]]:
    path = paths.config(file)
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()).get("mcpServers") or {})
    except ValueError:
        return {}


def _write(file: str, got: Dict[str, Dict[str, Any]]) -> None:
    paths.config(file).write_text(json.dumps({"mcpServers": got}, indent=2) + "\n")


def servers() -> Dict[str, Dict[str, Any]]:
    """The servers that are on."""
    return _read("mcp")


def off() -> Dict[str, Dict[str, Any]]:
    return _read("mcp-off")


def save(all_servers: Dict[str, Dict[str, Any]]) -> None:
    _write("mcp", all_servers)


def all_servers() -> List[Dict[str, Any]]:
    """Every server, on or off, by name."""
    rows = [{"name": n, "spec": s, "on": True} for n, s in servers().items()]
    rows += [{"name": n, "spec": s, "on": False} for n, s in off().items() if n not in servers()]
    return sorted(rows, key=lambda r: r["name"].lower())


def add(name: str, *, command: Optional[str] = None, args: Optional[List[str]] = None,
        url: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> None:
    """Add or replace a server; an env value given blank keeps the one it had."""
    name = (name or "").strip()
    if not name:
        raise ValueError("an MCP server needs a name")
    if not (command or url):
        raise ValueError("an MCP server needs a command or a url")
    spec: Dict[str, Any] = {"type": "http", "url": url} if url else {"command": command, "args": args or []}
    on, was_off = servers(), off()
    old = (on.get(name) or was_off.get(name) or {}).get("env") or {}
    kept = {k: (v if v else old.get(k, "")) for k, v in (env or {}).items()}
    kept = {k: v for k, v in kept.items() if v}
    if kept:
        spec["env"] = kept
    if name in was_off and name not in on:
        was_off[name] = spec
        _write("mcp-off", was_off)
    else:
        on[name] = spec
        save(on)


def remove(name: str) -> bool:
    gone = False
    for file in ("mcp", "mcp-off"):
        got = _read(file)
        if got.pop(name, None) is not None:
            _write(file, got)
            gone = True
    return gone


def set_on(name: str, on: bool) -> None:
    """Move a server between mcp.json (on) and mcp-off.json (off)."""
    src, dst = ("mcp-off", "mcp") if on else ("mcp", "mcp-off")
    a, b = _read(src), _read(dst)
    if name not in a:
        if name in b:
            return
        raise KeyError(f"no MCP server {name}")
    b[name] = a.pop(name)
    _write(dst, b)
    _write(src, a)


def mask(value: str) -> str:
    return "••••" + (value[-2:] if len(value) > 4 else "")


def public(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The spec with every env value masked — what may leave the server."""
    out = {k: v for k, v in spec.items() if k != "env"}
    if spec.get("env"):
        out["env"] = {k: mask(str(v)) for k, v in spec["env"].items()}
    return out


def programs(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Dict[str, bool]]:
    """Per server: does Claude / Codex get it — on, and that program configured and installed."""
    from . import capacity, db, providers
    own = conn is None
    conn = conn or db.connect()
    try:
        status = capacity.status(conn)
    finally:
        if own:
            conn.close()
    cfg = providers.config()
    ready = {prog: any(status.get(n, (False, ""))[0] for n, c in cfg.items() if c.get("kind") in kinds)
             for prog, kinds in PROGRAM_KINDS.items()}
    return {r["name"]: {prog: r["on"] and ok for prog, ok in ready.items()} for r in all_servers()}


def claude_config() -> Optional[str]:
    return str(paths.config("mcp")) if servers() else None


def codex_config() -> List[str]:
    return mcp_flags(servers())
