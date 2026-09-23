# SPDX-License-Identifier: Apache-2.0
"""One MCP registry, rendered into each CLI (ROADMAP, Stage 1).

Servers are declared once, in ~/.eki/mcp.json, and every backend gets a
view: Claude Code receives them per session as `--mcp-config` (its
`dynamic` scope — nothing is written into ~/.claude), Codex gets a managed
block in its config.toml between two marker lines, rewritten whole each
time so a hand-written server above or below it is never touched. Gemini
CLI reads servers only from its settings.json, which is JSON and can't
hold markers, so eki keeps a note of the entries it wrote there
(~/.eki/mcp-gemini.json) and replaces only those; a server of the same
name that someone else put there wins and is left alone.

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
GEMINI_SETTINGS = Path("~/.gemini/settings.json").expanduser()
#: the names eki wrote into Gemini's settings, so the next render replaces
#: exactly those
GEMINI_OWNED = HOME / "mcp-gemini.json"
BEGIN = "# --- eki: MCP servers (managed; edit with eki, not by hand) ---"
END = "# --- eki: end ---"
BACKENDS = ("claude", "codex", "gemini")
#: the sides there were before a server said which ones it is on for; one
#: added since gets every server until it is turned off there
FIRST_BACKENDS = ("claude", "codex")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: Servers eki knows how to add in one step: the command, the key it needs,
#: and what having it gives a backend (a capability the router filters on).
#: eki adds them; it doesn't write them — that's the whole point.
CATALOG: List[Dict[str, Any]] = [
    {"id": "brave-search", "title": "Brave Search", "provides": ["web"],
     "command": "npx -y @brave/brave-search-mcp-server", "key_env": "BRAVE_API_KEY",
     "blurb": "Web search through Brave's API (free tier available)."},
    {"id": "exa", "title": "Exa", "provides": ["web"],
     "command": "npx -y exa-mcp-server", "key_env": "EXA_API_KEY",
     "blurb": "Neural web search with full-page content."},
    {"id": "tavily", "title": "Tavily", "provides": ["web"],
     "command": "npx -y tavily-mcp", "key_env": "TAVILY_API_KEY",
     "blurb": "Search and extract, built for agents."},
    {"id": "perplexity", "title": "Perplexity Ask", "provides": ["web"],
     "command": "npx -y server-perplexity-ask", "key_env": "PERPLEXITY_API_KEY",
     "blurb": "Searched, sourced answers from Sonar."},
    {"id": "fetch", "title": "Fetch", "provides": ["fetch"],
     "command": "uvx mcp-server-fetch", "key_env": "",
     "blurb": "Read a web page as text (no search)."},
    {"id": "playwright", "title": "Playwright", "provides": ["browser"],
     "command": "npx -y @playwright/mcp@latest", "key_env": "",
     "blurb": "Drive a real browser: pages, forms, screenshots."},
    {"id": "github", "title": "GitHub", "provides": ["github"],
     "command": "npx -y @modelcontextprotocol/server-github", "key_env": "GITHUB_PERSONAL_ACCESS_TOKEN",
     "blurb": "Issues, pull requests, repositories."},
    {"id": "filesystem", "title": "Filesystem", "provides": ["files"],
     "command": "npx -y @modelcontextprotocol/server-filesystem ~", "key_env": "",
     "blurb": "Read and write files under a folder."},
]


def catalog_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    return next((dict(e) for e in CATALOG if e["id"] == entry_id), None)


def provides(backend: str, what: str) -> bool:
    """Whether a backend side (claude / codex / gemini) has an enabled server that
    provides a capability — the web, say. The router reads this so a local
    model with Codex's hands and a search server counts as one that can
    research; without one it doesn't, and research goes elsewhere."""
    for spec in load().values():
        if _on(spec, backend) and what in (spec.get("provides") or []):
            return True
    return False


def load() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    out = {k: v for k, v in (servers or {}).items() if isinstance(v, dict) and NAME_RE.match(k)}
    for spec in out.values():
        offered = spec.get("offered") or FIRST_BACKENDS
        on = set(spec.get("backends") or []) | {b for b in BACKENDS if b not in offered}
        spec["backends"] = [b for b in BACKENDS if b in on]
        spec["offered"] = list(BACKENDS)
    return out


def _on(spec: Dict[str, Any], backend: str) -> bool:
    return bool(spec.get("enabled", True)) and backend in (spec.get("backends") or [])


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
    out: Dict[str, Any] = {"type": kind, "backends": backends, "offered": list(BACKENDS),
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
    given = [str(p) for p in (spec.get("provides") or []) if p]
    if given:
        out["provides"] = given
    return out


def put(name: str, spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    servers = load()
    servers[name] = normalize(name, spec)
    save(servers)
    render(servers)
    return servers


def remove(name: str) -> Dict[str, Dict[str, Any]]:
    servers = load()
    servers.pop(name, None)
    save(servers)
    render(servers)
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
    render(servers)
    return servers


# ---- rendering -----------------------------------------------------------

def for_claude(servers: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The `--mcp-config` document: the servers enabled for Claude Code."""
    servers = load() if servers is None else servers
    out: Dict[str, Any] = {}
    for name, spec in servers.items():
        if not _on(spec, "claude"):
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


def render(servers: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    """Every CLI that keeps its servers in a file of its own: Codex, and
    Gemini CLI. Claude Code needs nothing written; it is given them per
    session."""
    render_codex(servers)
    render_gemini(servers)


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
        if not _on(spec, "codex"):
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
    one; everything outside the markers is kept byte for byte — except one
    top-level key, `web_search`, which turns on Codex's own hosted search
    and has to sit above the first table to be top-level at all."""
    path = path or CODEX_CONFIG
    block = codex_block(servers, eki_command)
    try:
        original = path.read_text()
    except OSError:
        original = ""
    current = _ensure_web_search(original)
    if BEGIN in current and END in current:
        head, _, rest = current.partition(BEGIN)
        _, _, tail = rest.partition(END + "\n")
        if not tail and rest.endswith(END):
            tail = ""
        new = head + block + tail
    else:
        new = current + ("\n" if current and not current.endswith("\n") else "") + "\n" + block
    if new != original:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    return new


_TOP_KEY = re.compile(r"^\s*web_search\s*=", re.M)
_FIRST_TABLE = re.compile(r"^\s*\[", re.M)


def _ensure_web_search(text: str) -> str:
    """`web_search = "live"` at the top of Codex's config unless the file
    already sets it (whatever it says stands). Codex's search is a hosted
    tool: the model gets it only when this is on."""
    from . import settings as settings_mod
    if not settings_mod.load().get("codex_web_search", True):
        return text
    head_end = _FIRST_TABLE.search(text)
    head = text[:head_end.start()] if head_end else text
    if _TOP_KEY.search(head):
        return text
    line = '# set by eki: Codex\'s hosted web search (Settings → Routing)\nweb_search = "live"\n'
    return line + text


def gemini_servers(servers: Optional[Dict[str, Dict[str, Any]]] = None,
                   eki_command: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """The `mcpServers` entries for Gemini CLI, in its own shape (`httpUrl`
    for streamable HTTP, `url` for SSE), plus eki's own tools as a stdio
    server, as Codex has them."""
    servers = load() if servers is None else servers
    out: Dict[str, Dict[str, Any]] = {}
    eki_cmd = eki_command if eki_command is not None else eki_stdio_command()
    if eki_cmd:
        root = str(Path(__file__).resolve().parent.parent)
        out["eki"] = {"command": eki_cmd[0], "args": list(eki_cmd[1:]),
                      "env": {"PYTHONPATH": root}}
    for name, spec in servers.items():
        if not _on(spec, "gemini"):
            continue
        if spec.get("type") == "stdio":
            entry: Dict[str, Any] = {"command": spec["command"], "args": list(spec.get("args") or [])}
            if spec.get("env"):
                entry["env"] = dict(spec["env"])
        else:
            entry = {"httpUrl" if spec.get("type") == "http" else "url": spec["url"]}
            if spec.get("headers"):
                entry["headers"] = dict(spec["headers"])
        out[name] = entry
    return out


def _gemini_owned() -> List[str]:
    try:
        names = json.loads(GEMINI_OWNED.read_text()).get("servers")
    except (OSError, ValueError, AttributeError):
        return []
    return [str(n) for n in names or [] if isinstance(n, str)]


def render_gemini(servers: Optional[Dict[str, Dict[str, Any]]] = None,
                  path: Optional[Path] = None,
                  eki_command: Optional[List[str]] = None) -> Dict[str, Any]:
    """Put the registry's servers into Gemini CLI's settings.json, replacing
    what eki wrote there last time and nothing else. Returns what was
    done: the names written, and the ones someone else holds. Nothing is
    written when Gemini CLI has never run here (no ~/.gemini), or when its
    file can't be read as JSON — a file eki can't parse is not eki's to
    rewrite."""
    path = path or GEMINI_SETTINGS
    report: Dict[str, Any] = {"written": [], "conflicts": []}
    if not path.parent.is_dir():
        return report
    try:
        original = path.read_text()
    except FileNotFoundError:
        original = ""
    except OSError:
        return report
    try:
        before = json.loads(original) if original.strip() else {}
    except ValueError:
        return {**report, "error": f"{path} isn't plain JSON; left alone"}
    settings = json.loads(json.dumps(before))
    current = settings.get("mcpServers", {}) if isinstance(settings, dict) else None
    if not isinstance(current, dict):
        return {**report, "error": f"{path} has no usable mcpServers; left alone"}
    owned = set(_gemini_owned())
    kept = {k: v for k, v in current.items() if k not in owned}
    for name, entry in gemini_servers(servers, eki_command).items():
        if name in kept:
            report["conflicts"].append(name)
            continue
        kept[name] = entry
        report["written"].append(name)
    if kept or "mcpServers" in settings:
        settings["mcpServers"] = kept
    if settings != before:
        path.write_text(json.dumps(settings, indent=2) + "\n")
    if sorted(owned) != sorted(report["written"]):
        GEMINI_OWNED.parent.mkdir(parents=True, exist_ok=True)
        GEMINI_OWNED.write_text(json.dumps({"servers": report["written"]}, indent=2))
    return report


def eki_stdio_command() -> List[str]:
    """How another program starts eki's tool server: this interpreter,
    this package. The bundled app and a dev checkout both resolve here."""
    return [sys.executable, "-m", "eki.cli", "mcp"]


def codex_screen_off() -> List[str]:
    """Flags for a Codex whose thread mustn't use the screen (a goal's that
    may not): eki's tool server started without the screen tools. Only when
    this Codex has eki's server — a `-c` for one it hasn't would make half a
    server it can't start."""
    try:
        if "[mcp_servers.eki]" not in CODEX_CONFIG.read_text():
            return []
    except OSError:
        return []
    root = str(Path(__file__).resolve().parent.parent)
    return ["-c", "mcp_servers.eki.env={ PYTHONPATH = " + _toml_str(root) + ', EKI_SCREEN = "0" }']


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
    render(servers)
    return servers
