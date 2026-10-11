"""Claude Code, the genuine program on your own login, in its two-way mode.

`claude --input-format stream-json --output-format stream-json` reads JSON
lines as well as writing them — the mode its own SDK uses. eki opens with a
handshake, sends the turn, and reads: `system/init` (the session id),
`assistant` messages (text, thinking, tool calls), `rate_limit_event`s (the
plan's windows) and a final `result`. When Claude asks you something — a
question (`AskUserQuestion`), a permission, an MCP server's form — a
`control_request` arrives; it becomes an ask, and your answer goes back as
the `control_response`. eki never reads a token; the program runs as you.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from . import oneshot
from .base import Emit, Outcome, Turn, find_binary, image_block, short, with_history
from .program import Channel, ProgramProvider

#: tools that ask *you* something, rather than ask permission to do something
QUESTION_TOOLS = {"AskUserQuestion"}
INIT = "eki-init"


class ClaudeCode(ProgramProvider):
    kind = "claude_code"
    interactive = True

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.bin = find_binary(cfg.get("binary", "claude"))

    def available(self) -> tuple:
        return (True, "") if self.bin else (False, "claude is not installed")

    def argv(self, turn: Turn) -> List[str]:
        argv = [self.bin or "claude", "--output-format", "stream-json", "--verbose",
                "--input-format", "stream-json", "--permission-prompt-tool", "stdio"]
        if turn.extra.get("allowed_tools"):
            # an allow-list (a bare component agent): those tools and nothing else, no asking
            argv += ["--permission-mode", "dontAsk", "--allowedTools", ",".join(turn.extra["allowed_tools"])]
        elif self.cfg.get("permissions", "auto") == "auto":
            # the top of the run tree may do anything (docs/design.md); questions
            # for you still come through the prompt tool
            argv += ["--permission-mode", "bypassPermissions", "--allow-dangerously-skip-permissions"]
        else:
            argv += ["--permission-mode", self.cfg.get("permission_mode", "default")]
        model = turn.extra.get("model") or self.cfg.get("model")   # a run's own model wins
        if model:
            argv += ["--model", model]
        if turn.extra.get("effort"):                               # the run's effort; none: the model's own
            argv += ["--effort", turn.extra["effort"]]
        if turn.resume:
            argv += ["--resume", turn.resume]
        if turn.extra.get("bare"):
            return argv + self._bare(turn)
        if turn.cwd:
            argv += ["--add-dir", turn.cwd]
        for flag, key in (("--plugin-dir", "claude_plugin"), ("--mcp-config", "claude_mcp"),
                          ("--append-system-prompt", "note")):
            value = turn.extra.get(key)
            for v in (value if isinstance(value, list) else [value] if value else []):
                argv += [flag, v]
        if turn.extra.get("deny_tools"):
            argv += ["--disallowedTools", ",".join(turn.extra["deny_tools"])]
        return argv

    @staticmethod
    def _bare(turn: Turn) -> List[str]:
        """A bare agent (docs/design.md, Components): its own system prompt and nothing else —
        no setting sources, skills, slash commands or built-in tools; only its listed MCP servers."""
        argv = ["--system-prompt", turn.extra.get("system_prompt") or "",
                "--setting-sources", "", "--strict-mcp-config", "--tools", "",
                "--disable-slash-commands"]
        if turn.extra.get("claude_mcp"):
            argv += ["--mcp-config", turn.extra["claude_mcp"]]
        if turn.extra.get("deny_tools"):
            argv += ["--disallowedTools", ",".join(turn.extra["deny_tools"])]
        return argv

    # ---- the conversation ----------------------------------------------------------------

    def opening(self, ch: Channel, turn: Turn) -> None:
        ch.write({"type": "control_request", "request_id": INIT, "request": {"subtype": "initialize"}})

    def _send_turn(self, ch: Channel) -> None:
        if ch.state.get("sent"):
            return
        ch.state["sent"] = True
        content: List[Dict[str, Any]] = [{"type": "text", "text": with_history(ch.turn)}]
        for path in ch.turn.images:
            media, data = image_block(path)
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        ch.write({"type": "user", "uuid": str(uuid.uuid4()), "session_id": "",
                  "message": {"role": "user", "content": content},
                  "parent_tool_use_id": None})

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        kind = event.get("type")
        if kind == "control_response":
            resp = event.get("response") or {}
            if resp.get("request_id") == INIT:
                self._send_turn(ch)
            return
        if kind == "control_request":
            self._asked(event, ch)
            return
        if kind == "control_cancel_request":
            ch.retract(event.get("request_id"))
            return
        if kind == "system" and event.get("subtype") == "init":
            self._send_turn(ch)             # an older program may not answer the handshake
            if event.get("session_id"):
                emit("session", {"id": event["session_id"], "model": event.get("model")})
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    emit("text", {"text": block["text"]})
                elif btype == "thinking" and block.get("thinking"):
                    emit("thinking", {"text": block["thinking"]})
                elif btype == "tool_use":
                    if block.get("id"):
                        ch.state.setdefault("open_tools", {})[block["id"]] = block.get("name", "")
                    inp = block.get("input") or {}
                    tool = {"name": block.get("name", ""), "detail": short(inp)}
                    path = inp.get("file_path") or inp.get("notebook_path")
                    if path and block.get("name") in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                        tool["path"] = path       # a file it made or changed (M3: shown in the thread)
                    if isinstance(inp.get("url"), str):
                        tool["url"] = inp["url"]  # whole, for the journal's check of where it went
                    emit("tool", tool)
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    ch.state.get("open_tools", {}).pop(block.get("tool_use_id"), None)
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            windows = info.get("unifiedWindows") or {}
            if windows:
                emit("quota", {"provider": self.name, "windows": {
                    k: {"used": float(v.get("utilization") or 0), "resets_at": _epoch(v.get("resetsAt"))}
                    for k, v in windows.items() if isinstance(v, dict)}})
            if str(info.get("status", "")).lower() == "rejected":
                out.reset_at = _epoch(info.get("resetsAt") or info.get("resets_at"))
                emit("note", {"text": f"Claude limit reached ({info.get('rateLimitType', 'limit')})"})
                out.error = out.error or "usage limit reached"
        elif kind == "result":
            if event.get("usage"):
                emit("usage", {"usage": event["usage"], "cost": event.get("total_cost_usd")})
            if ch.state.get("open_tools"):
                # a tool call of this turn still waits for its result: this `result` came
                # from elsewhere (a background task of an earlier turn) — the turn goes on
                return
            if event.get("is_error"):
                out.error = str(event.get("result") or event.get("subtype") or "error")[:400]
            out.finished = True

    def ended(self, out: Outcome, ch: Channel) -> None:
        """The program exited: if a tool call never got its result, the turn was cut off."""
        open_tools = ch.state.get("open_tools") or {}
        if open_tools and not out.finished and out.state == "done":
            names = ", ".join(sorted(set(n or "?" for n in open_tools.values())))
            out.state = "failed"
            out.error = f"the turn was cut off mid tool call ({names}): it never got its result"

    def _asked(self, event: Dict[str, Any], ch: Channel) -> None:
        req = event.get("request") or {}
        rid = event.get("request_id", "")
        sub = req.get("subtype")
        key = {"rid": rid, "tool_use_id": req.get("tool_use_id"), "input": req.get("input") or {}}
        if sub == "can_use_tool" and req.get("tool_name") in QUESTION_TOOLS:
            ch.ask("question", {"questions": (req.get("input") or {}).get("questions") or []}, key)
        elif sub == "can_use_tool":
            ch.ask("permission", {"tool": req.get("tool_name", ""),
                                  "title": req.get("title") or req.get("display_name") or "",
                                  "description": req.get("description") or "",
                                  "detail": short(req.get("input"), 600),
                                  "can_always": bool(req.get("permission_suggestions"))},
                   {**key, "suggestions": req.get("permission_suggestions") or []})
        elif sub == "elicitation":
            ch.ask("form", {"server": req.get("mcp_server_name", ""), "message": req.get("message", ""),
                            "url": req.get("url") or "", "schema": req.get("requested_schema") or {}},
                   {**key, "elicitation": True})
        else:
            # hooks and servers eki doesn't host: say no, politely
            ch.write({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": f"eki doesn't handle {sub}"}})

    def answer(self, ch: Channel, key: Any, response: Dict[str, Any]) -> None:
        """`response` is in eki's shape: {"allow": bool, "always": bool, "answers": {...},
        "message": str, "content": {...}} — translated to what Claude Code expects."""
        allow = bool(response.get("allow", True))
        if key.get("elicitation"):
            body: Dict[str, Any] = {"action": "accept" if allow else "decline"}
            if allow and response.get("content"):
                body["content"] = response["content"]
        elif not allow:
            body = {"behavior": "deny", "message": response.get("message") or "The user declined this."}
        else:
            updated = dict(key.get("input") or {})
            if "answers" in response:
                updated["answers"] = response["answers"]
            body = {"behavior": "allow", "updatedInput": updated}
            if response.get("always") and key.get("suggestions"):
                body["updatedPermissions"] = key["suggestions"]
        if key.get("tool_use_id") and not key.get("elicitation"):
            body["toolUseID"] = key["tool_use_id"]
        ch.write({"type": "control_response",
                  "response": {"subtype": "success", "request_id": key["rid"], "response": body}})

    def complete(self, messages: List[Dict[str, Any]], *, tools: Optional[list] = None,
                 max_tokens: int = 8192, timeout: float = 600, stop=None, on_text=None,
                 model: Optional[str] = None, effort: Optional[str] = None) -> Dict[str, Any]:
        """One answer with no tools (oneshot): {"text", "tool_calls": []}."""
        return oneshot.complete("claude", self.bin or "claude", messages, model=model or self.cfg.get("model"),
                                effort=effort, timeout=timeout, stop=stop, on_text=on_text)

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        if turn.extra.get("row") == "chore":      # eki's own job: one call, no tools
            return oneshot.chore(self, turn, emit)
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
