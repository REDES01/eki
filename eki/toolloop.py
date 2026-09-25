# SPDX-License-Identifier: Apache-2.0
"""A local model's own tool loop, so it can call eki too (ROADMAP, Stage 8).

A model with no tools of its own is given one — hand the thread to a harness
(eki/handoff.py). A server that speaks OpenAI's `tools` (mlx_lm.server) can
be given a few more: the ones that *are* integration, the same ones Claude
Code and Codex get from eki (eki/mcpbridge.py) — ask another model on this
Mac, make a picture, see what the Mac can do. The model calls one, eki runs
it, the result goes back to the model as a `tool` message, and it carries on
until it answers in words. Asked for a village's lore and a portrait of its
elder, the local model writes the lore and asks for the portrait itself; it
doesn't hand the whole thread to a subscription for a picture.

Not a harness: no files, no commands, no screen — those are what hand_off is
for. The loop is bounded (STEPS rounds of calls), what is asked goes below
this run with its depth and budget (eki/nesting.py), and every call is said
in the answer's meta and as it happens. Setting `local_tools` (on).
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

from .adapters.base import Message, ToolCall

#: eki's tools a local model is offered: reaching other providers, never the screen
OFFERED = ("eki_capabilities", "eki_ask", "eki_image")
#: rounds of calls before the model is asked to answer with what it has
STEPS = 4
#: how much of a tool's answer goes back to the model
RESULT_CHARS = 12000


def specs(bridge: Any) -> List[Dict[str, Any]]:
    """eki's offered tools, in OpenAI's `tools` shape."""
    out = []
    for name in OFFERED:
        t = bridge.tools.get(name)
        if t is not None:
            out.append({"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.schema}})
    return out


def instructions() -> str:
    """What the model is told beside its tools."""
    return (
        "Through eki you can also reach the other models on this Mac: eki_image makes a "
        "picture, eki_ask asks another model for one step (a different model's view, a longer "
        "piece of writing, a model that can read a repo), and eki_capabilities says who is "
        "here. Use them for a part you can't do yourself, then answer the person with what "
        "came back — don't call them for what you can answer, and don't say you made "
        "something you didn't.")


def _result_text(result: Dict[str, Any]) -> str:
    """An MCP tool result as the text a model reads."""
    parts = []
    for block in result.get("content") or []:
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") == "image":
            parts.append("[an image; its path follows]")
    body = "\n".join(p for p in parts if p).strip() or "(nothing)"
    if result.get("isError"):
        body = "error: " + body
    return body[:RESULT_CHARS]


def _pictures(result: Dict[str, Any]) -> List[str]:
    """The files a picture tool saved, from its "saved at …" lines."""
    out = []
    for block in result.get("content") or []:
        t = str(block.get("text") or "") if block.get("type") == "text" else ""
        if t.startswith("saved at "):
            out.append(t[len("saved at "):].strip())
    return out


def _said(call: ToolCall) -> str:
    """One line for the person: what the model asked eki for."""
    args = call.arguments or {}
    what = str(args.get("prompt") or "").strip()
    what = " ".join(what.split())
    if len(what) > 120:
        what = what[:117] + "…"
    to = f" {args['backend']}" if args.get("backend") else ""
    return {"eki_capabilities": "Looking at what this Mac can do",
            "eki_ask": f"Asking{to or ' another model'}: {what}",
            "eki_image": f"Drawing: {what}"}.get(call.name, f"Calling {call.name}")


class Loop:
    """A backend whose turn is the model's tool loop: in every other way it
    is the backend it wraps (its usage, its session, its close)."""

    def __init__(self, backend: Any, bridge: Any, meta: Optional[Dict[str, Any]] = None,
                 steps: int = STEPS):
        self.backend = backend
        self.bridge = bridge
        self.meta = meta if meta is not None else {}
        self.steps = steps

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    async def stream(self, messages: List[Message], **kw: Any) -> AsyncIterator[Any]:
        """The model's answer; a call to one of eki's tools is run and its
        result given back, and the model asked again. Any other call (hand_off)
        passes through to whoever offered it."""
        ours = specs(self.bridge)
        names = {s["function"]["name"] for s in ours}
        others = list(kw.get("tools") or [])
        history = list(messages)
        if history and history[0].role == "system":
            history[0] = Message("system", history[0].content + "\n\n" + instructions())
        else:
            history.insert(0, Message("system", instructions()))
        n = 0
        for step in range(self.steps + 1):
            # the last round has only what the caller offered: time to answer
            offered = others + (ours if step < self.steps else [])
            said: List[str] = []
            calls: List[ToolCall] = []
            async for chunk in self.backend.stream(history, **{**kw, "tools": offered}):
                if isinstance(chunk, ToolCall) and chunk.name in names:
                    calls.append(chunk)
                    continue
                if isinstance(chunk, str):
                    said.append(chunk)
                yield chunk
            if not calls:
                return
            ids = []
            for c in calls:
                n += 1
                ids.append(c.id or f"call_{n}")
            history.append(Message("assistant", "".join(said), tool_calls=[
                {"id": i, "type": "function",
                 "function": {"name": c.name, "arguments": c.raw or json.dumps(c.arguments)}}
                for i, c in zip(ids, calls)]))
            for i, c in zip(ids, calls):
                yield {"kind": "activity", "text": _said(c)}
                result = await self.bridge.call(c.name, dict(c.arguments or {}))
                self.meta.setdefault("eki_tools", []).append(
                    {"tool": c.name, "prompt": str((c.arguments or {}).get("prompt") or "")[:200],
                     "error": bool(result.get("isError"))})
                for path in _pictures(result):
                    # the picture is part of the answer, where the person sees it
                    yield f"\n\n![]({path.replace(' ', '%20')})\n\n"
                history.append(Message("tool", _result_text(result), tool_call_id=i))
