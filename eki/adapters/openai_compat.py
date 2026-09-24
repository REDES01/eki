# SPDX-License-Identifier: Apache-2.0
"""Anything that speaks OpenAI's /v1/chat/completions.

That covers OpenAI itself, xAI, OpenRouter, Ollama, LM Studio, vLLM and
llama.cpp's server. Paid ones need `options.secret = true`; the engine then
puts the Keychain key in `api_key` for the life of one run and nowhere else.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import Backend, BackendError, Health, Message, register


@register("openai_compat")
class OpenAICompatBackend(Backend):
    PRODUCES = ("code", "prose")

    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.base = self.options.get("base_url", "http://127.0.0.1:8000/v1").rstrip("/")
        self.model: Optional[str] = self.options.get("model")
        self.timeout = float(self.options.get("timeout_seconds", 300))
        self.last_usage: Dict[str, Any] = {}

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = self.options.get("api_key")
        if key:
            h["Authorization"] = f"Bearer {key}"
        if "openrouter.ai" in self.base:
            h["X-Title"] = "eki"
        return h

    async def list_models(self) -> List[str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/models", headers=self._headers())
        if r.status_code != 200:
            raise BackendError(f"HTTP {r.status_code}: {r.text[:200]}")
        return [m.get("id") for m in r.json().get("data", []) if m.get("id")]

    async def health(self) -> Health:
        try:
            names = await self.list_models()
        except (httpx.HTTPError, BackendError, ValueError) as e:
            return Health(False, str(e)[:200])
        if self.model and names and self.model not in names:
            return Health(False, f"{self.model} is not offered here")
        return Health(True, self.model or f"{len(names)} models")

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        model = self.model
        if not model:
            # a local server with one thing loaded doesn't need to be told;
            # a hosted API does, and will say so
            try:
                names = await self.list_models()
                model = names[0] if len(names) == 1 else None
            except (httpx.HTTPError, BackendError, ValueError):
                model = None
        body: Dict[str, Any] = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if model:
            body["model"] = model
        for name in ("temperature", "max_tokens"):
            value = kw.get(name, self.options.get(name))
            if value is not None:
                body[name] = value
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", f"{self.base}/chat/completions",
                                    json=body, headers=self._headers()) as r:
                    if r.status_code != 200:
                        detail = (await r.aread())[:300].decode("utf-8", "replace")
                        raise BackendError(f"{self.key} HTTP {r.status_code}: {detail}")
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
                        if event.get("usage"):
                            self.last_usage = event["usage"]
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        text = (choices[0].get("delta") or {}).get("content")
                        if text:
                            yield text
        except httpx.HTTPError as e:
            raise BackendError(f"{self.key} request failed: {e}") from e
