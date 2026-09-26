"""A model served on this Mac over the OpenAI-compatible API (MLX, llama.cpp,
Ollama, LM Studio).

A bare model, not a harness — with one tool, the only one eki gives a
model: `handoff`, which passes the thread to an agent with hands (Claude
Code, Codex) when the request needs files, commands, the web or this Mac.
It answers what it can and hands off what it can't.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .base import Emit, Outcome, Provider, Turn

SYSTEM = (
    "You are a model running locally on the user's Mac, inside eki. You can answer, "
    "explain, write and translate. You cannot read or change files, run commands, browse "
    "the web or see the screen. If the request needs any of that — a code change in a "
    "project, a build, anything about this Mac's files, current information from the web — "
    "call the `handoff` tool at once instead of answering, and an agent with tools will take "
    "the thread. Otherwise just answer.")

HANDOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "handoff",
        "description": "Pass this request to a coding agent that can use files, commands and the web.",
        "parameters": {"type": "object",
                       "properties": {"reason": {"type": "string",
                                                 "description": "what the request needs"}},
                       "required": ["reason"]},
    },
}

DEFAULT_PARAMS = {"temperature": 1.0, "top_p": 0.95, "top_k": 20}


class Local(Provider):
    kind = "local"

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.base = cfg.get("base_url", "http://127.0.0.1:8080").rstrip("/")

    def models(self, timeout: float = 1.5) -> List[str]:
        with urllib.request.urlopen(self.base + "/v1/models", timeout=timeout) as r:
            data = json.load(r)
        return [m.get("id") for m in data.get("data") or [] if m.get("id")]

    def available(self) -> tuple:
        try:
            return (True, "") if self.models() else (False, "no model loaded")
        except (OSError, ValueError) as e:
            return False, f"not running at {self.base} ({type(e).__name__})"

    def model(self) -> str:
        return self.cfg.get("model") or (self.models() or ["default"])[0]

    def complete(self, messages: List[Dict[str, Any]], *, tools: Optional[list] = None,
                 max_tokens: int = 8192, timeout: float = 600, stop=None, on_text=None) -> Dict[str, Any]:
        """One streamed chat completion: {"text", "tool_calls"}."""
        body: Dict[str, Any] = {"model": self.model(), "messages": messages, "stream": True,
                                "max_tokens": max_tokens,
                                "chat_template_kwargs": {"enable_thinking": bool(self.cfg.get("thinking"))}}
        body.update(self.cfg.get("params") or DEFAULT_PARAMS)
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        text: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if stop is not None and stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                        if on_text:
                            on_text(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(tc.get("index", 0)), {"name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
        return {"text": "".join(text), "tool_calls": list(calls.values())}

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        messages = [{"role": "system", "content": SYSTEM}, *turn.history,
                    {"role": "user", "content": turn.prompt}]
        buffer: List[str] = []

        def flush() -> None:
            if buffer:
                emit("text", {"text": "".join(buffer)})
                buffer.clear()

        def on_text(piece: str) -> None:
            buffer.append(piece)
            if sum(len(b) for b in buffer) > 200 or piece.endswith("\n"):
                flush()

        try:
            got = self.complete(messages, tools=[HANDOFF_TOOL], stop=turn.stop, on_text=on_text)
        except (OSError, urllib.error.URLError, ValueError) as e:
            return Outcome("failed", f"{self.name}: {e}")
        if turn.stop.is_set():
            return Outcome("cancelled", "stopped")
        for call in got["tool_calls"]:
            if call["name"] == "handoff":
                return Outcome("handed_off", reason=_reason(call["arguments"]))
        if "<tool_call>" in got["text"] and "handoff" in got["text"]:
            # a server that doesn't parse tool calls hands them back as text
            return Outcome("handed_off", reason="the model asked for an agent")
        flush()
        return Outcome("done")


def _reason(arguments: str) -> str:
    try:
        return str(json.loads(arguments or "{}").get("reason") or "")
    except ValueError:
        return arguments or ""
