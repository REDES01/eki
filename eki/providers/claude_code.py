"""Claude Code, the genuine program on your own login, run headless.

`claude -p … --output-format stream-json --verbose` prints one JSON object
per line: `system/init` (the session id), `assistant` messages (text and
tool calls), `rate_limit_event`s, and a final `result`. eki never reads a
token; the program is run as you, like in a terminal.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import Emit, Outcome, ProgramProvider, Turn, find_binary, short, with_history


class ClaudeCode(ProgramProvider):
    kind = "claude_code"

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.bin = find_binary(cfg.get("binary", "claude"))

    def available(self) -> tuple:
        return (True, "") if self.bin else (False, "claude is not installed")

    def argv(self, turn: Turn) -> List[str]:
        argv = [self.bin or "claude", "-p", with_history(turn),
                "--output-format", "stream-json", "--verbose",
                # the top of the run tree may do anything (docs/design.md)
                "--dangerously-skip-permissions"]
        if self.cfg.get("model"):
            argv += ["--model", self.cfg["model"]]
        if turn.resume:
            argv += ["--resume", turn.resume]
        if turn.cwd:
            argv += ["--add-dir", turn.cwd]
        plugin = turn.extra.get("claude_plugin")
        if plugin:
            argv += ["--plugin-dir", plugin]
        mcp = turn.extra.get("claude_mcp")
        if mcp:
            argv += ["--mcp-config", mcp]
        note = turn.extra.get("note")
        if note:
            argv += ["--append-system-prompt", note]
        return argv

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init" and event.get("session_id"):
            emit("session", {"id": event["session_id"], "model": event.get("model")})
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    emit("text", {"text": block["text"]})
                elif btype == "thinking" and block.get("thinking"):
                    emit("thinking", {"text": block["thinking"]})
                elif btype == "tool_use":
                    emit("tool", {"name": block.get("name", ""), "detail": short(block.get("input"))})
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if str(info.get("status", "")).lower() == "rejected":
                out.reset_at = _epoch(info.get("resetsAt") or info.get("resets_at"))
                emit("note", {"text": f"Claude limit reached ({info.get('rateLimitType', 'limit')})"})
                out.error = out.error or "usage limit reached"
        elif kind == "result":
            if event.get("usage"):
                emit("usage", {"usage": event["usage"], "cost": event.get("total_cost_usd")})
            if event.get("is_error"):
                out.error = str(event.get("result") or event.get("subtype") or "error")[:400]

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        out = super().take(turn, emit)
        if out.state == "failed" and turn.resume and "no conversation found" in out.error.lower():
            # the session never made it to disk (killed before its first save):
            # start a fresh one with the thread as context
            emit("note", {"text": "earlier session not found; starting a new one"})
            turn.resume = None
            turn.history = turn.extra.get("full_history", turn.history)
            return super().take(turn, emit)
        return out


def _epoch(value: Any) -> Any:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v / 1000 if v > 1e12 else v
