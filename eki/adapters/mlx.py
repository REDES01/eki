# SPDX-License-Identifier: Apache-2.0
"""Local models served by mlx_lm, over its OpenAI-compatible endpoint.

Free and private, so the router reaches for this first whenever a request
doesn't need tools, a repo or a large context.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List

import httpx

from .base import Backend, BackendError, Health, Message, ToolCall, register


@register("mlx")
class MLXBackend(Backend):
    PRODUCES = ("code", "prose")

    #: takes OpenAI's `tools` and streams `tool_calls` back — mlx_lm.server
    #: parses the model's own call format (Qwen's <tool_call>) into them
    accepts_tools = True

    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.base = self.options.get("base_url", "http://127.0.0.1:8080").rstrip("/")
        self.model = self.options.get("model")          # None = whatever is loaded
        self.timeout = float(self.options.get("timeout_seconds", 300))
        self.last_usage: Dict[str, Any] = {}

    async def health(self) -> Health:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base}/v1/models")
            if r.status_code != 200:
                return Health(False, f"HTTP {r.status_code}")
            names = [m.get("id") for m in r.json().get("data", [])]
            return Health(True, ", ".join(n for n in names if n) or "no model loaded")
        except httpx.HTTPError as e:
            return Health(False, f"unreachable: {e}")

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        body: Dict[str, Any] = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # ask for the final usage frame; servers that don't know this
            # option ignore it rather than failing
            "stream_options": {"include_usage": True},
            "temperature": kw.get("temperature", self.options.get("temperature", 0.7)),
        }
        if self.model:
            body["model"] = self.model
        if kw.get("max_tokens") or self.options.get("max_tokens"):
            body["max_tokens"] = kw.get("max_tokens") or self.options["max_tokens"]
        if kw.get("tools") and self.accepts_tools:
            body["tools"] = kw["tools"]
        # a call may arrive in pieces (by index), and is only whole at the end
        calls: Dict[int, Dict[str, str]] = {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", f"{self.base}/v1/chat/completions",
                                    json=body) as r:
                    if r.status_code != 200:
                        detail = (await r.aread())[:200].decode("utf-8", "replace")
                        raise BackendError(f"mlx HTTP {r.status_code}: {detail}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk in ("", "[DONE]"):
                            continue
                        try:
                            event = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        # the final frame carries usage and no choices
                        if event.get("usage"):
                            self.last_usage = event["usage"]
                        try:
                            delta = event["choices"][0].get("delta", {})
                        except (KeyError, IndexError):
                            continue
                        text = delta.get("content")
                        if text:
                            yield text
                        for tc in delta.get("tool_calls") or []:
                            at = calls.setdefault(int(tc.get("index") or 0), {"name": "", "args": ""})
                            fn = tc.get("function") or {}
                            at["name"] += fn.get("name") or ""
                            at["args"] += fn.get("arguments") or ""
        except httpx.HTTPError as e:
            raise BackendError(f"mlx request failed: {e}") from e
        for _, c in sorted(calls.items()):
            if not c["name"]:
                continue
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
                yield ToolCall(c["name"], args if isinstance(args, dict) else {}, "")
            except json.JSONDecodeError:
                yield ToolCall(c["name"], {}, c["args"])


@register("llamacpp")
class LlamaCppBackend(MLXBackend):
    """llama.cpp's server speaks the same OpenAI-shaped endpoint: a GGUF
    model set up by eki (see eki/engines/llamacpp.py) is served through it."""
    # its server only parses tool calls when started with --jinja, which
    # eki's llama.cpp setup doesn't promise: these models hand over by marker
    accepts_tools = False
