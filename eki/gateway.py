# SPDX-License-Identifier: Apache-2.0
"""eki as a model provider for Codex: the harness drives a local model.

A local model on this Mac has no harness of its own — no tools, no file
editing, no agent loop. Codex has one, and takes any provider that speaks
OpenAI's Responses API. So eki speaks it, here, on its own port, for every
local model it manages: Codex calls /v1/responses with the model's provider
key, eki starts the model if it's asleep, translates the request to the
chat-completions call the local server understands, and turns the stream
back into the Responses events Codex expects — text as it arrives, tool
calls as items. Registering a model with Codex is therefore nothing the
user does: a local model added to eki is a model Codex can drive.

Only what Codex needs is implemented; this is not a general Responses API.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

import httpx

THINK = re.compile(r"<think>.*?</think>\s*", re.S)


# ---- request: Responses → chat completions ---------------------------------

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def to_chat(body: Dict[str, Any], model: str) -> Dict[str, Any]:
    """The chat-completions request for a Responses request. Namespaced and
    hosted tools (multi-agent, web search) are Codex's own and dropped: a
    local model gets its function tools and nothing it can't use."""
    messages: List[Dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": str(body["instructions"])})
    items = body.get("input")
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    for item in items or []:
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role", "user")
            text = _text(item.get("content"))
            if role in ("developer", "system"):
                # local servers take one system message, first: Codex's
                # developer notes join the instructions there
                if messages and messages[0]["role"] == "system" and len(messages) == 1:
                    messages[0]["content"] += "\n\n" + text
                elif messages and messages[0]["role"] == "system":
                    messages[0]["content"] += "\n\n" + text
                else:
                    messages.insert(0, {"role": "system", "content": text})
                continue
            messages.append({"role": role, "content": text})
        elif kind == "function_call":
            call = {"id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": item.get("name", ""),
                                 "arguments": item.get("arguments") or "{}"}}
            if messages and messages[-1]["role"] == "assistant" and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""),
                             "content": _text(item.get("output")) if not isinstance(item.get("output"), str)
                             else item["output"]})
        # reasoning and anything else: not something a local model takes back
    tools = [{"type": "function",
              "function": {"name": t.get("name", ""), "description": t.get("description", ""),
                           "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
             for t in body.get("tools") or [] if t.get("type") == "function"]
    out: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        out["tools"] = tools
        out["tool_choice"] = "auto" if body.get("tool_choice") in (None, "auto") else body["tool_choice"]
    if body.get("max_output_tokens"):
        out["max_tokens"] = int(body["max_output_tokens"])
    return out


# ---- response: chat chunks → Responses events -------------------------------

def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps({'type': event, **data})}\n\n"


class Translator:
    """Feeds on parsed chat-completion chunks, yields Responses SSE frames."""

    def __init__(self, model: str, prompt_chars: int = 0):
        self.model = model
        self.response_id = f"resp_{uuid.uuid4().hex[:16]}"
        self.output: List[Dict[str, Any]] = []
        self.index = -1
        self.message_id = ""
        self.text = ""
        self.pending = ""                     # text held back while inside <think>
        self.thinking = False
        self.calls: Dict[str, Dict[str, Any]] = {}
        self.prompt_chars = prompt_chars
        self.usage: Optional[Dict[str, int]] = None
        self.seq = 0

    def _ev(self, event: str, data: Dict[str, Any]) -> str:
        self.seq += 1
        return _sse(event, {"sequence_number": self.seq, **data})

    def start(self) -> Iterable[str]:
        base = {"id": self.response_id, "object": "response", "model": self.model,
                "created_at": int(time.time()), "output": []}
        yield self._ev("response.created", {"response": {**base, "status": "in_progress"}})
        yield self._ev("response.in_progress", {"response": {**base, "status": "in_progress"}})

    def _open_message(self) -> Iterable[str]:
        if self.message_id:
            return
        self.index += 1
        self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        yield self._ev("response.output_item.added", {
            "output_index": self.index,
            "item": {"id": self.message_id, "type": "message", "role": "assistant",
                     "status": "in_progress", "content": []}})

    def _close_message(self) -> Iterable[str]:
        if not self.message_id:
            return
        item = {"id": self.message_id, "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": self.text, "annotations": []}]}
        yield self._ev("response.output_text.done", {
            "output_index": self.index, "content_index": 0, "item_id": self.message_id,
            "text": self.text})
        yield self._ev("response.output_item.done", {"output_index": self.index, "item": item})
        self.output.append(item)
        self.message_id, self.text = "", ""

    def _visible(self, delta: str) -> str:
        """Text minus the model's <think> blocks, across chunk boundaries."""
        buf = self.pending + delta
        out = ""
        while buf:
            if self.thinking:
                end = buf.find("</think>")
                if end < 0:
                    self.pending = buf[-8:]          # a tag may straddle the boundary
                    return out
                buf = buf[end + len("</think>"):].lstrip()
                self.thinking = False
            start = buf.find("<think>")
            if start < 0:
                # keep a possible partial "<think" for the next chunk
                cut = max(0, len(buf) - 6) if "<" in buf[-6:] else len(buf)
                out += buf[:cut]
                self.pending = buf[cut:]
                return out
            out += buf[:start]
            buf = buf[start + len("<think>"):]
            self.thinking = True
        self.pending = ""
        return out

    def feed(self, chunk: Dict[str, Any]) -> Iterable[str]:
        if chunk.get("usage"):
            u = chunk["usage"]
            self.usage = {"input_tokens": int(u.get("prompt_tokens", 0)),
                          "output_tokens": int(u.get("completion_tokens", 0))}
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                shown = self._visible(content)
                if shown:
                    yield from self._open_message()
                    self.text += shown
                    yield self._ev("response.output_text.delta", {
                        "output_index": self.index, "content_index": 0,
                        "item_id": self.message_id, "delta": shown})
            for call in delta.get("tool_calls") or []:
                fn = call.get("function") or {}
                cid = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                if fn.get("name"):
                    yield from self._close_message()
                    self.index += 1
                    item = {"id": f"fc_{uuid.uuid4().hex[:12]}", "type": "function_call",
                            "call_id": cid, "name": fn["name"], "arguments": "",
                            "status": "in_progress", "output_index": self.index}
                    self.calls[cid] = item
                    yield self._ev("response.output_item.added", {
                        "output_index": self.index,
                        "item": {k: v for k, v in item.items() if k != "output_index"}})
                item = self.calls.get(cid) or next(iter(self.calls.values()), None)
                if item is not None and fn.get("arguments"):
                    item["arguments"] += fn["arguments"]
                    yield self._ev("response.function_call_arguments.delta", {
                        "output_index": item["output_index"], "item_id": item["id"],
                        "delta": fn["arguments"]})

    def finish(self, error: str = "") -> Iterable[str]:
        if error:
            yield self._ev("response.failed", {"response": {
                "id": self.response_id, "object": "response", "status": "failed",
                "error": {"code": "server_error", "message": error}}})
            return
        if self.pending and not self.thinking:
            self.text += self.pending
            yield from self._open_message()
            yield self._ev("response.output_text.delta", {
                "output_index": self.index, "content_index": 0,
                "item_id": self.message_id, "delta": self.pending})
            self.pending = ""
        yield from self._close_message()
        for item in self.calls.values():
            done = {k: v for k, v in item.items() if k != "output_index"}
            done["status"] = "completed"
            if not done["arguments"]:
                done["arguments"] = "{}"
            yield self._ev("response.function_call_arguments.done", {
                "output_index": item["output_index"], "item_id": item["id"],
                "arguments": done["arguments"]})
            yield self._ev("response.output_item.done", {"output_index": item["output_index"],
                                                         "item": done})
            self.output.append(done)
        usage = self.usage or {
            # the server didn't say: a rough count, so Codex's meter moves
            "input_tokens": self.prompt_chars // 4,
            "output_tokens": sum(len(o.get("arguments", "")) for o in self.output) // 4
            + sum(len(c.get("text", "")) for o in self.output for c in o.get("content", [])) // 4}
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["input_tokens_details"] = {"cached_tokens": 0}
        usage["output_tokens_details"] = {"reasoning_tokens": 0}
        yield self._ev("response.completed", {"response": {
            "id": self.response_id, "object": "response", "model": self.model,
            "status": "completed", "output": self.output, "usage": usage}})


async def chat_chunks(base_url: str, request: Dict[str, Any],
                      timeout: float = 600.0) -> AsyncIterator[Dict[str, Any]]:
    """Parsed chunks of a streaming chat completion from a local server."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", base_url.rstrip("/") + "/chat/completions",
                                 json=request) as r:
            if r.status_code != 200:
                body = (await r.aread())[:300].decode("utf-8", "replace")
                raise RuntimeError(f"local server {r.status_code}: {body}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except ValueError:
                    continue


async def respond(base_url: str, model: str, body: Dict[str, Any]) -> AsyncIterator[str]:
    """The whole exchange, as SSE frames for Codex."""
    request = to_chat(body, model)
    t = Translator(body.get("model", model), prompt_chars=len(json.dumps(request["messages"])))
    for frame in t.start():
        yield frame
    try:
        async for chunk in chat_chunks(base_url, request):
            for frame in t.feed(chunk):
                yield frame
    except (httpx.HTTPError, RuntimeError, OSError) as e:
        for frame in t.finish(error=str(e)[:300]):
            yield frame
        return
    for frame in t.finish():
        yield frame
