# SPDX-License-Identifier: Apache-2.0
"""Claude through the Anthropic API, with the user's own API key.

This is the metered path — an `x-api-key` the user pasted, billed per token.
It has nothing to do with a Claude Code subscription login, which eki never
touches; that one is reached only by running the user's `claude` CLI.

No model name is hard-coded: without `options.model`, the first model the
API lists (it lists newest first) is used.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import Backend, BackendError, Health, Message, register

VERSION = "2023-06-01"


@register("anthropic_api")
class AnthropicAPIBackend(Backend):
    PRODUCES = ("code", "prose")

    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.base = self.options.get("base_url", "https://api.anthropic.com").rstrip("/")
        self.model: Optional[str] = self.options.get("model")
        self.timeout = float(self.options.get("timeout_seconds", 600))
        self.last_usage: Dict[str, Any] = {}

    def _headers(self) -> Dict[str, str]:
        key = self.options.get("api_key")
        if not key:
            raise BackendError("no Anthropic API key")
        return {"x-api-key": key, "anthropic-version": VERSION,
                "content-type": "application/json"}

    async def list_models(self) -> List[str]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base}/v1/models", headers=self._headers())
        if r.status_code != 200:
            raise BackendError(f"HTTP {r.status_code}: {r.text[:200]}")
        return [m["id"] for m in r.json().get("data", []) if m.get("id")]

    async def health(self) -> Health:
        try:
            names = await self.list_models()
        except (httpx.HTTPError, BackendError, ValueError) as e:
            return Health(False, str(e)[:200])
        return Health(True, self.model or (names[0] if names else "no models"))

    async def _model(self) -> str:
        if self.model:
            return self.model
        names = await self.list_models()
        if not names:
            raise BackendError("the API listed no models")
        self.model = names[0]
        return self.model

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content}
                 for m in messages if m.role in ("user", "assistant")]
        try:
            body: Dict[str, Any] = {
                "model": await self._model(),
                "max_tokens": int(kw.get("max_tokens") or self.options.get("max_tokens", 8192)),
                "messages": turns,
                "stream": True,
            }
            if system:
                body["system"] = system
            temperature = kw.get("temperature", self.options.get("temperature"))
            if temperature is not None:
                body["temperature"] = temperature
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", f"{self.base}/v1/messages",
                                    json=body, headers=self._headers()) as r:
                    if r.status_code != 200:
                        detail = (await r.aread())[:300].decode("utf-8", "replace")
                        raise BackendError(f"anthropic HTTP {r.status_code}: {detail}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        kind = event.get("type")
                        if kind == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            self.last_usage.update(usage)
                        elif kind == "message_delta":
                            self.last_usage.update(event.get("usage") or {})
                        elif kind == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield delta["text"]
                        elif kind == "error":
                            raise BackendError(
                                f"anthropic: {(event.get('error') or {}).get('message', event)}")
        except httpx.HTTPError as e:
            raise BackendError(f"anthropic request failed: {e}") from e
