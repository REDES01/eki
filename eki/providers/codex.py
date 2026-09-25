"""Codex CLI, the genuine program on your own login, run headless.

`codex exec --json` prints one event per line. The vocabulary has moved
between releases, so reading is loose: known shapes are used, the rest is
ignored rather than guessed at.

    {"type":"thread.started","thread_id":"…"}
    {"type":"item.completed","item":{"type":"agent_message","text":"…"}}
    {"type":"item.completed","item":{"type":"command_execution","command":"…"}}
    {"type":"turn.completed","usage":{…}}
    {"type":"turn.failed","error":{"message":"…"}}
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .base import Emit, Outcome, ProgramProvider, Turn, find_binary, short, with_history


class Codex(ProgramProvider):
    kind = "codex"

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.bin = find_binary(cfg.get("binary", "codex"))

    def available(self) -> tuple:
        return (True, "") if self.bin else (False, "codex is not installed")

    def argv(self, turn: Turn) -> List[str]:
        argv = [self.bin or "codex", "exec"]
        if turn.resume:
            argv += ["resume", turn.resume]
        # the top of the run tree may do anything (docs/design.md)
        argv += ["--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"]
        if self.cfg.get("model"):
            argv += ["-m", self.cfg["model"]]
        for flag in turn.extra.get("codex_config") or []:
            argv += ["-c", flag]
        note = turn.extra.get("note")
        if note:
            argv += ["-c", f"developer_instructions={json.dumps(note)}"]
        argv.append(with_history(turn))
        return argv

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome) -> None:
        kind = str(event.get("type") or "")
        sid = event.get("thread_id") or event.get("session_id")
        if kind in ("thread.started", "session.created") and sid:
            emit("session", {"id": str(sid)})
            return
        if kind == "item.completed":
            item = event.get("item") or {}
            itype = str(item.get("type") or "")
            if itype == "agent_message" and (item.get("text") or item.get("message")):
                emit("text", {"text": item.get("text") or item.get("message")})
            elif itype == "reasoning" and item.get("text"):
                emit("thinking", {"text": item["text"]})
            elif itype == "command_execution":
                emit("tool", {"name": "shell", "detail": short(item.get("command"))})
            elif itype == "file_change":
                emit("tool", {"name": "edit", "detail": short(item.get("changes") or item.get("path"))})
            elif itype in ("mcp_tool_call", "web_search"):
                emit("tool", {"name": item.get("tool") or itype,
                              "detail": short(item.get("arguments") or item.get("query") or "")})
            elif itype == "error" and item.get("message"):
                emit("note", {"text": short(item["message"], 300)})
            return
        if kind == "turn.completed" and isinstance(event.get("usage"), dict):
            emit("usage", {"usage": event["usage"]})
            out.error = ""                     # a turn that completed isn't failed by earlier noise
            return
        if kind in ("turn.failed", "error"):
            err = event.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or event.get("message")
            out.error = short(msg or kind, 400)


def mcp_flags(servers: Dict[str, Dict[str, Any]]) -> List[str]:
    """The registry as `-c` overrides, so nothing is written to ~/.codex."""
    flags: List[str] = []
    for name, spec in servers.items():
        key = f"mcp_servers.{name}"
        if spec.get("url"):
            flags.append(f"{key}.url={json.dumps(spec['url'])}")
            continue
        flags.append(f"{key}.command={json.dumps(spec.get('command', ''))}")
        if spec.get("args"):
            flags.append(f"{key}.args={json.dumps(list(spec['args']))}")
        env = spec.get("env") or {}
        if env:
            inner = ", ".join(f"{k} = {json.dumps(str(v))}" for k, v in env.items())
            flags.append(f"{key}.env={{{inner}}}")
    return flags
