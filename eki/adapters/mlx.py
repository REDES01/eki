# SPDX-License-Identifier: Apache-2.0
"""Local models served by mlx_lm, over its OpenAI-compatible endpoint.

Free and private, so the router reaches for this first whenever a request
doesn't need tools, a repo or a large context.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List

import httpx

from .base import Backend, BackendError, Health, Message, register


@register("mlx")
class MLXBackend(Backend):
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
        except httpx.HTTPError as e:
            raise BackendError(f"mlx request failed: {e}") from e
