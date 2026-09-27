"""The answer half of the Responses adapter: chat-completion chunks in,
the Responses SSE events Codex reads out.

`Translator` is fed parsed chunks one at a time and hands back SSE frames.
A reply is at most one message (text) and any number of tool calls, each an
output item with its `added` and `done`. The model's `<think>` blocks are
dropped on the way, even when a tag is split across chunks.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set

OPEN, CLOSE = "<think>", "</think>"


def frame(event: str, data: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps({'type': event, **data}, ensure_ascii=False)}\n\n".encode()


def _held(buf: str, tag: str) -> int:
    """How many trailing characters of `buf` could be the start of `tag`."""
    for n in range(min(len(tag) - 1, len(buf)), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0


class Think:
    """Strips <think>…</think> from streamed text, tags split anywhere."""

    def __init__(self) -> None:
        self.inside = False
        self.hold = ""
        self.trim = False          # drop the whitespace that follows a closed block

    def feed(self, text: str) -> str:
        buf, self.hold, out = self.hold + text, "", []
        while buf:
            tag = CLOSE if self.inside else OPEN
            at = buf.find(tag)
            if at < 0:
                keep = _held(buf, tag)
                if not self.inside:
                    out.append(self._trimmed(buf[:len(buf) - keep]))
                self.hold = buf[len(buf) - keep:]
                break
            if not self.inside:
                out.append(self._trimmed(buf[:at]))
            buf = buf[at + len(tag):]
            self.inside = not self.inside
            self.trim = not self.inside
        return "".join(out)

    def flush(self) -> str:
        rest, self.hold = ("" if self.inside else self._trimmed(self.hold)), ""
        return rest

    def _trimmed(self, s: str) -> str:
        if self.trim:
            s = s.lstrip()
            self.trim = not s
        return s


class Translator:
    """Chat chunks → Responses events. `custom` names the tools Codex declared
    as freeform (`custom`): their calls go back as `custom_tool_call` items
    carrying the model's `input` argument."""

    def __init__(self, model: str, custom: Optional[Set[str]] = None, prompt_chars: int = 0):
        self.model = model
        self.custom = custom or set()
        self.prompt_chars = prompt_chars
        self.id = f"resp_{uuid.uuid4().hex[:24]}"
        self.seq = 0
        self.think = Think()
        self.items: List[Dict[str, Any]] = []        # every item, in output order
        self.message: Optional[Dict[str, Any]] = None
        self.calls: Dict[Any, Dict[str, Any]] = {}   # open tool calls by chunk index
        self.last_key: Any = None
        self.usage: Optional[Dict[str, int]] = None

    def _ev(self, event: str, **data: Any) -> bytes:
        self.seq += 1
        return frame(event, {"sequence_number": self.seq, **data})

    def _response(self, status: str, **more: Any) -> Dict[str, Any]:
        return {"id": self.id, "object": "response", "created_at": int(time.time()),
                "model": self.model, "status": status, **more}

    def start(self) -> Iterable[bytes]:
        yield self._ev("response.created", response=self._response("in_progress", output=[]))
        yield self._ev("response.in_progress", response=self._response("in_progress", output=[]))

    # ---- the message ---------------------------------------------------------------------

    def _text(self, text: str) -> Iterable[bytes]:
        if not text:
            return
        if self.message is None:
            self.message = {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant",
                            "status": "in_progress", "content": [], "_index": len(self.items), "_text": ""}
            self.items.append(self.message)
            idx = self.message["_index"]
            yield self._ev("response.output_item.added", output_index=idx, item=_public(self.message))
            yield self._ev("response.content_part.added", output_index=idx, item_id=self.message["id"],
                           content_index=0, part={"type": "output_text", "text": "", "annotations": []})
        self.message["_text"] += text
        yield self._ev("response.output_text.delta", output_index=self.message["_index"],
                       item_id=self.message["id"], content_index=0, delta=text)

    def _close_message(self) -> Iterable[bytes]:
        m, self.message = self.message, None
        if m is None:
            return
        part = {"type": "output_text", "text": m["_text"], "annotations": []}
        m.update(status="completed", content=[part])
        idx = m["_index"]
        yield self._ev("response.output_text.done", output_index=idx, item_id=m["id"], content_index=0,
                       text=m["_text"])
        yield self._ev("response.content_part.done", output_index=idx, item_id=m["id"], content_index=0,
                       part=part)
        yield self._ev("response.output_item.done", output_index=idx, item=_public(m))

    # ---- tool calls ----------------------------------------------------------------------

    def _key(self, call: Dict[str, Any]) -> Any:
        if call.get("index") is not None:
            return call["index"]
        if call.get("id"):
            return call["id"]
        if (call.get("function") or {}).get("name") or self.last_key is None:
            return f"k{len(self.calls)}"
        return self.last_key

    def _call(self, call: Dict[str, Any]) -> Iterable[bytes]:
        fn = call.get("function") or {}
        key = self._key(call)
        self.last_key = key
        item = self.calls.get(key)
        if item is None:
            yield from self._close_message()
            name = fn.get("name") or ""
            item = {"id": f"fc_{uuid.uuid4().hex[:24]}", "status": "in_progress",
                    "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:24]}", "name": name,
                    "_index": len(self.items), "_args": ""}
            if name in self.custom:
                item.update(type="custom_tool_call", input="")
            else:
                item.update(type="function_call", arguments="")
            self.calls[key] = item
            self.items.append(item)
            yield self._ev("response.output_item.added", output_index=item["_index"], item=_public(item))
        elif fn.get("name") and not item["name"]:
            item["name"] = fn["name"]
        delta = fn.get("arguments") or ""
        if delta:
            item["_args"] += delta
            if item["type"] == "function_call":
                yield self._ev("response.function_call_arguments.delta", output_index=item["_index"],
                               item_id=item["id"], delta=delta)

    def _close_call(self, item: Dict[str, Any]) -> Iterable[bytes]:
        item["status"] = "completed"
        idx = item["_index"]
        if item["type"] == "custom_tool_call":
            item["input"] = _custom_input(item["_args"])
            yield self._ev("response.custom_tool_call_input.done", output_index=idx, item_id=item["id"],
                           input=item["input"])
        else:
            item["arguments"] = item["_args"] or "{}"
            yield self._ev("response.function_call_arguments.done", output_index=idx, item_id=item["id"],
                           arguments=item["arguments"])
        yield self._ev("response.output_item.done", output_index=idx, item=_public(item))

    # ---- the stream ----------------------------------------------------------------------

    def feed(self, chunk: Dict[str, Any]) -> Iterable[bytes]:
        u = chunk.get("usage")
        if isinstance(u, dict):
            self.usage = {"input_tokens": int(u.get("prompt_tokens") or 0),
                          "output_tokens": int(u.get("completion_tokens") or 0)}
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                yield from self._text(self.think.feed(delta["content"]))
            for call in delta.get("tool_calls") or []:
                yield from self._call(call)

    def finish(self) -> Iterable[bytes]:
        yield from self._text(self.think.flush())
        yield from self._close_message()
        for item in self.calls.values():
            yield from self._close_call(item)
        self.calls = {}
        output = [_public(i) for i in self.items]
        yield self._ev("response.completed", response=self._response("completed", output=output,
                                                                     usage=self._usage(output)))

    def fail(self, message: str) -> Iterable[bytes]:
        yield self._ev("response.failed", response=self._response(
            "failed", output=[], error={"code": "server_error", "message": message}))

    def _usage(self, output: List[Dict[str, Any]]) -> Dict[str, Any]:
        u = self.usage or {
            # the server didn't count: a rough estimate, so Codex's meter still moves
            "input_tokens": self.prompt_chars // 4,
            "output_tokens": len(json.dumps(output)) // 4}
        return {**u, "total_tokens": u["input_tokens"] + u["output_tokens"],
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0}}


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _custom_input(args: str) -> str:
    try:
        got = json.loads(args or "{}")
    except ValueError:
        return args
    if isinstance(got, dict) and isinstance(got.get("input"), str):
        return got["input"]
    return args
