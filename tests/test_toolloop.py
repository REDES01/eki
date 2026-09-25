# SPDX-License-Identifier: Apache-2.0
"""A local model's own tool loop: it calls eki's tools, gets the results back,
and answers — without handing the thread to a harness."""
import asyncio
import json

import pytest

from eki import mcpbridge, toolloop
from eki.adapters.base import Backend, BackendInfo, Health, Message, ToolCall
from tests.test_handoff import Harness, eng, say  # noqa: F401  (the fixture)


class FakeBridge:
    """eki's tools, answering from a script."""

    def __init__(self):
        self.tools = mcpbridge.Bridge(None, screen=False).tools
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "eki_image":
            return {"content": [{"type": "image", "data": "", "mimeType": "image/png"},
                                {"type": "text", "text": "saved at /tmp/elder portrait.png"}]}
        return {"content": [{"type": "text", "text": "The elder is called Maren."}]}


class Scripted(Backend):
    """Calls a tool on its first round, answers on the next."""

    def __init__(self, first):
        super().__init__(BackendInfo(key="qwen", kind="mlx", label="Qwen"), {})
        self.first, self.seen = first, []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        self.seen.append((list(messages), kw.get("tools")))
        if messages[-1].role != "tool":
            yield "Let me check. "
            for c in self.first:
                yield c
        else:
            yield f"Done: {messages[-1].content}"


async def turn(backend, bridge, **kw):
    loop = toolloop.Loop(backend, bridge, meta := {})
    out = [c async for c in loop.stream([Message("user", "the village elder")], **kw)]
    return out, meta


def test_a_call_is_run_and_its_result_given_back():
    b, bridge = Scripted([ToolCall("eki_ask", {"prompt": "name the elder"}, "", "c1")]), FakeBridge()
    out, meta = asyncio.run(turn(b, bridge))
    assert bridge.calls == [("eki_ask", {"prompt": "name the elder"})]
    text = "".join(c for c in out if isinstance(c, str))
    assert text == "Let me check. Done: The elder is called Maren."
    assert {"kind": "activity", "text": "Asking another model: name the elder"} in out
    history, tools = b.seen[1]
    assert history[0].role == "system" and "eki_image" in history[0].content
    assert history[-2].tool_calls[0]["id"] == "c1" and history[-2].content == "Let me check. "
    assert history[-1].role == "tool" and history[-1].tool_call_id == "c1"
    assert {t["function"]["name"] for t in tools} == {"eki_ask", "eki_image", "eki_capabilities"}
    assert meta["eki_tools"] == [{"tool": "eki_ask", "prompt": "name the elder", "error": False}]


def test_a_picture_lands_in_the_answer():
    b, bridge = Scripted([ToolCall("eki_image", {"prompt": "the elder"})]), FakeBridge()
    out, _ = asyncio.run(turn(b, bridge))
    assert "\n\n![](/tmp/elder%20portrait.png)\n\n" in out
    assert b.seen[1][0][-1].content.startswith("[an image; its path follows]")


def test_hand_off_passes_through_to_whoever_offered_it():
    call = ToolCall("hand_off", {"target": "claude_code", "brief": "build it"})
    b, bridge = Scripted([call]), FakeBridge()
    offered = [{"type": "function", "function": {"name": "hand_off"}}]
    out, _ = asyncio.run(turn(b, bridge, tools=offered))
    assert out[-1] == call and bridge.calls == []
    assert b.seen[0][1][0]["function"]["name"] == "hand_off"      # still offered, first


def test_the_loop_ends_with_only_the_callers_tools():
    class Always(Scripted):
        async def stream(self, messages, **kw):
            self.seen.append((list(messages), kw.get("tools")))
            if kw.get("tools"):
                yield ToolCall("eki_capabilities", {})
            else:
                yield "What I have."
    b, bridge = Always([]), FakeBridge()
    out = asyncio.run(turn(b, bridge))[0]
    assert len(bridge.calls) == toolloop.STEPS and out[-1] == "What I have."
    assert b.seen[-1][1] == []


def test_the_mlx_adapter_sends_calls_and_their_answers():
    import httpx
    from eki.adapters.mlx import MLXBackend
    sent = {}
    frames = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_9", "function": {
        "name": "eki_ask", "arguments": '{"prompt": "hi"}'}}]}}]}]

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, text="".join(f"data: {json.dumps(f)}\n\n" for f in frames))
    real = httpx.AsyncClient
    b = MLXBackend(BackendInfo(key="qwen", kind="mlx", label="Qwen"), {})
    history = [Message("user", "hi"),
               Message("assistant", "", tool_calls=[{"id": "call_1", "type": "function",
                                                     "function": {"name": "eki_ask", "arguments": "{}"}}]),
               Message("tool", "hello", tool_call_id="call_1")]

    async def run():
        httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler))
        try:
            return [c async for c in b.stream(history)]
        finally:
            httpx.AsyncClient = real
    out = asyncio.run(run())
    assert sent["messages"][0] == {"role": "user", "content": "hi"}
    assert sent["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert sent["messages"][2] == {"role": "tool", "content": "hello", "tool_call_id": "call_1"}
    assert out == [ToolCall("eki_ask", {"prompt": "hi"}, "", "call_9")]


# ---- in a thread -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_local_model_asks_eki_and_answers_in_its_own_thread(eng, monkeypatch):  # noqa: F811
    seen = []

    class Calling(Backend):
        accepts_tools = True

        async def health(self):
            return Health(True)

        async def stream(self, messages, **kw):
            seen.append((list(messages), kw.get("tools") or []))
            last = messages[-1]
            if last.role == "tool":
                yield f"Here is what I found: {last.content}"
            elif "haiku" in last.content.lower():
                yield ToolCall("eki_capabilities", {}, "", "c1")
            else:
                yield "A plain answer."
    from eki.adapters import base as adapters
    monkeypatch.setattr(adapters, "build",
                        lambda info, opts: (Harness if info.kind == "claude_code" else Calling)(info, opts))
    cid, run, turn = await say(eng, "write a haiku about rain")
    assert run["backend"] == "qwen"                                  # it stayed local
    names = [t["function"]["name"] for t in seen[0][1]]
    assert names[0] == "hand_off" and "eki_ask" in names and "eki_screenshot" not in names
    assert turn["content"].startswith("Here is what I found: Backends on this Mac")
    assert json.loads(turn["meta"])["eki_tools"][0]["tool"] == "eki_capabilities"

    eng.settings = {**eng.settings, "local_tools": False}
    await say(eng, "and now?", cid)
    assert [t["function"]["name"] for t in seen[-1][1]] == ["hand_off"]
    await eng.runner.stop()
