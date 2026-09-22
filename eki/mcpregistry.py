# SPDX-License-Identifier: Apache-2.0
"""One MCP registry, rendered into each CLI (ROADMAP, Stage 4).

Servers are declared once, in ~/.eki/mcp.json, and every backend gets a
view: Claude Code receives them per session as `--mcp-config` (its
`dynamic` scope — nothing is written into ~/.claude), Codex gets a managed
block in its config.toml between two marker lines, rewritten whole each
time so a hand-written server above or below it is never touched.

The servers Claude Code already knows from its own files (user, project,
claude.ai connectors) are not copied here; they show in the panel with
their scope and can be imported with one click, which is the only way a
server enters this file from that side.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path("~/.eki").expanduser()
PATH = HOME / "mcp.json"
CODEX_CONFIG = Path("~/.codex/config.toml").expanduser()
BEGIN = "# --- eki: MCP servers (managed; edit with eki, not by hand) ---"
END = "# --- eki: end ---"
BACKENDS = ("claude", "codex")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def load() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    return {k: v for k, v in (servers or {}).items() if isinstance(v, dict) and NAME_RE.match(k)}


def save(servers: Dict[str, Dict[str, Any]]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"servers": servers}, indent=2))


def normalize(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """A server as eki keeps it. `command` may be one string ("npx -y x")
    or a command plus `args`; a `url` makes it remote."""
    if not NAME_RE.match(name or ""):
        raise ValueError("a server name is letters, digits, dots, dashes or underscores")
    if name in ("eki", "computer-use"):
        raise ValueError(f"{name} is a server eki provides itself")
    url = str(spec.get("url") or "").strip()
    command = spec.get("command") or ""
    args = list(spec.get("args") or [])
    if isinstance(command, str) and command.strip() and not args and " " in command.strip():
        parts = shlex.split(command)
        command, args = parts[0], parts[1:]
    kind = str(spec.get("type") or ("http" if url else "stdio"))
    if kind not in ("stdio", "http", "sse"):
        raise ValueError("type is stdio, http or sse")
    if kind == "stdio" and not str(command).strip():
        raise ValueError("a stdio server needs a command")
    if kind != "stdio" and not url:
        raise ValueError("a remote server needs a url")
    backends = [b for b in (spec.get("backends") or list(BACKENDS)) if b in BACKENDS]
    out: Dict[str, Any] = {"type": kind, "backends": backends,
                           "enabled": bool(spec.get("enabled", True))}
    if kind == "stdio":
        out["command"] = str(command).strip()
        out["args"] = [str(a) for a in args]
        env = spec.get("env") or {}
        if isinstance(env, dict) and env:
            out["env"] = {str(k): str(v) for k, v in env.items()}
    else:
        out["url"] = url
        headers = spec.get("headers") or {}
        if isinstance(headers, dict) and headers:
            out["headers"] = {str(k): str(v) for k, v in headers.items()}
    if spec.get("origin"):
        out["origin"] = str(spec["origin"])
    return out


def put(name: str, spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    servers = load()
    servers[name] = normalize(name, spec)
    save(servers)
    render_codex(servers)
    return servers


def remove(name: str) -> Dict[str, Dict[str, Any]]:
    servers = load()
    servers.pop(name, None)
    save(servers)
    render_codex(servers)
    return servers


def set_enabled(name: str, enabled: bool, backend: str = "") -> Dict[str, Dict[str, Any]]:
    servers = load()
    spec = servers.get(name)
    if spec is None:
        raise KeyError(name)
    if backend:
        on = set(spec.get("backends") or [])
        (on.add if enabled else on.discard)(backend)
        spec["backends"] = [b for b in BACKENDS if b in on]
    else:
        spec["enabled"] = bool(enabled)
    save(servers)
    render_codex(servers)
    return servers


# ---- rendering -----------------------------------------------------------

def for_claude(servers: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The `--mcp-config` document: the servers enabled for Claude Code."""
    servers = load() if servers is None else servers
    out: Dict[str, Any] = {}
    for name, spec in servers.items():
        if not spec.get("enabled", True) or "claude" not in (spec.get("backends") or []):
            continue
        if spec.get("type") == "stdio":
            entry: Dict[str, Any] = {"type": "stdio", "command": spec["command"],
                                     "args": list(spec.get("args") or [])}
            if spec.get("env"):
                entry["env"] = dict(spec["env"])
        else:
            entry = {"type": spec.get("type", "http"), "url": spec["url"]}
            if spec.get("headers"):
                entry["headers"] = dict(spec["headers"])
        out[name] = entry
    return {"mcpServers": out}


def builtin_for_claude(claude_bin: str) -> Dict[str, Any]:
    """Claude Code's own computer-use server, which the terminal lists under
    "Built-in MCPs" and a headless session doesn't get on its own. The
    binary can run it as a stdio server (`claude --computer-use-mcp`), so
    eki declares it — when Settings → Computer use is on, on a Mac. The
    program drops that name from a config file, so it goes in after the
    handshake with `mcp_set_servers` (LiveSession.extra_servers), under the
    terminal's own name so its prompt about `mcp__computer-use__*` holds.
    Same 24 tools as the terminal; macOS's Accessibility and Screen
    Recording prompts apply to the process that runs it."""
    from . import settings as settings_mod
    if sys.platform != "darwin" or not claude_bin:
        return {}
    cfg = settings_mod.load()
    if not cfg.get("claude_screen", True) or not cfg.get("claude_builtin_computer_use", False):
        return {}
    return {"computer-use": {"type": "stdio", "command": claude_bin, "args": ["--computer-use-mcp"]}}


def claude_argv(servers: Optional[Dict[str, Dict[str, Any]]] = None,
                claude_bin: str = "") -> List[str]:
    doc = for_claude(servers)
    if not doc["mcpServers"]:
        return []
    return ["--mcp-config", json.dumps(doc)]


def _toml_str(s: str) -> str:
    return json.dumps(str(s))          # JSON string escaping is valid TOML basic-string escaping


def codex_block(servers: Optional[Dict[str, Dict[str, Any]]] = None,
                eki_command: Optional[List[str]] = None) -> str:
    """The managed TOML block for Codex: the registry's servers enabled for
    it, plus eki's own tools as a stdio server (Claude Code has those
    in-process; Codex runs `eki mcp`)."""
    servers = load() if servers is None else servers
    lines = [BEGIN]
    eki_cmd = eki_command if eki_command is not None else eki_stdio_command()
    if eki_cmd:
        # started from any folder: the package has to be importable from there
        root = str(Path(__file__).resolve().parent.parent)
        lines += ["[mcp_servers.eki]", f"command = {_toml_str(eki_cmd[0])}",
                  "args = [" + ", ".join(_toml_str(a) for a in eki_cmd[1:]) + "]",
                  "env = { PYTHONPATH = " + _toml_str(root) + " }",
                  "startup_timeout_sec = 30", ""]
    for name, spec in servers.items():
        if not spec.get("enabled", True) or "codex" not in (spec.get("backends") or []):
            continue
        lines.append(f"[mcp_servers.{name}]" if NAME_RE.match(name) and "." not in name
                     else f'[mcp_servers.{_toml_str(name)}]')
        if spec.get("type") == "stdio":
            lines.append(f"command = {_toml_str(spec['command'])}")
            lines.append("args = [" + ", ".join(_toml_str(a) for a in spec.get("args") or []) + "]")
            if spec.get("env"):
                lines.append("env = { " + ", ".join(f"{k} = {_toml_str(v)}" for k, v in spec["env"].items()) + " }")
        else:
            lines.append(f"url = {_toml_str(spec['url'])}")
            if spec.get("headers"):
                lines.append("http_headers = { " + ", ".join(f"{k} = {_toml_str(v)}" for k, v in spec["headers"].items()) + " }")
        lines.append("")
    lines.append(END)
    return "\n".join(lines) + "\n"


def render_codex(servers: Optional[Dict[str, Dict[str, Any]]] = None,
                 path: Optional[Path] = None, eki_command: Optional[List[str]] = None) -> str:
    """Write the managed block into Codex's config, replacing the previous
    one; everything outside the markers is kept byte for byte."""
    path = path or CODEX_CONFIG
    block = codex_block(servers, eki_command)
    try:
        current = path.read_text()
    except OSError:
        current = ""
    if BEGIN in current and END in current:
        head, _, rest = current.partition(BEGIN)
        _, _, tail = rest.partition(END + "\n")
        if not tail and rest.endswith(END):
            tail = ""
        new = head + block + tail
    else:
        new = current + ("\n" if current and not current.endswith("\n") else "") + "\n" + block
    if new != current:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    return new


def eki_stdio_command() -> List[str]:
    """How another program starts eki's tool server: this interpreter,
    this package. The bundled app and a dev checkout both resolve here."""
    return [sys.executable, "-m", "eki.cli", "mcp"]


def import_from_claude(status: List[Dict[str, Any]], names: List[str]) -> Dict[str, Dict[str, Any]]:
    """Take servers Claude Code already has (from mcp_status, with their
    config) into the registry, so Codex gets them too."""
    servers = load()
    by_name = {s.get("name"): s for s in status if isinstance(s, dict)}
    for name in names:
        s = by_name.get(name)
        cfg = (s or {}).get("config") or {}
        if not s or not isinstance(cfg, dict) or cfg.get("type") in ("sdk", "claudeai-proxy"):
            continue
        spec = {"type": cfg.get("type") or ("http" if cfg.get("url") else "stdio"),
                "command": cfg.get("command", ""), "args": cfg.get("args") or [],
                "env": cfg.get("env") or {}, "url": cfg.get("url", ""),
                "headers": cfg.get("headers") or {}, "origin": f"claude:{s.get('scope') or 'user'}"}
        try:
            servers[name] = normalize(name, spec)
        except ValueError:
            continue
    save(servers)
    render_codex(servers)
    return servers
