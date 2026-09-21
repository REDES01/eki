# SPDX-License-Identifier: Apache-2.0
"""eki as a Responses-API provider: Codex's harness over a local model."""
import asyncio
import json

from eki import gateway


def frames_of(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
        data = json.loads(lines[1][6:])
        assert data["type"] == lines[0][7:]
        out.append(data)
    return out


# ---- the request ------------------------------------------------------------

def test_a_codex_request_becomes_a_chat_request_with_only_function_tools():
    body = {
        "model": "qwen", "instructions": "You are Codex.", "stream": True,
        "input": [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<env>"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "call_id": "call_1", "name": "exec_command",
             "arguments": "{\"cmd\": \"ls\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "a.py\nb.py"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "two files"}]},
        ],
        "tools": [
            {"type": "function", "name": "exec_command", "description": "run", "parameters": {"type": "object"}},
            {"type": "namespace", "name": "multi_agent_v1"},
            {"type": "web_search"},
        ],
    }
    chat = gateway.to_chat(body, "mlx-community/Qwen3.8-27B-4bit")
    roles = [m["role"] for m in chat["messages"]]
    # one system message, first — local servers accept no other placement
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert chat["messages"][0]["content"] == "You are Codex.\n\n<env>"
    assert chat["messages"][2]["tool_calls"][0] == {
        "id": "call_1", "type": "function",
        "function": {"name": "exec_command", "arguments": "{\"cmd\": \"ls\"}"}}
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"}
    assert [t["function"]["name"] for t in chat["tools"]] == ["exec_command"]
    assert chat["model"] == "mlx-community/Qwen3.8-27B-4bit" and chat["stream"] is True


# ---- the stream ----------------------------------------------------------------

def _chunks(*deltas, usage=None):
    out = [{"choices": [{"delta": d, "finish_reason": None}]} for d in deltas]
    out.append({"choices": [{"delta": {}, "finish_reason": "stop"}], **({"usage": usage} if usage else {})})
    return out


def _run(chunks, model="qwen"):
    t = gateway.Translator(model, prompt_chars=400)
    text = "".join(t.start())
    for c in chunks:
        text += "".join(t.feed(c))
    text += "".join(t.finish())
    return frames_of(text)


def test_text_streams_as_one_message_item():
    got = _run(_chunks({"content": "Hel"}, {"content": "lo."},
                       usage={"prompt_tokens": 120, "completion_tokens": 3}))
    kinds = [f["type"] for f in got]
    assert kinds == ["response.created", "response.in_progress", "response.output_item.added",
                     "response.output_text.delta", "response.output_text.delta",
                     "response.output_text.done", "response.output_item.done", "response.completed"]
    done = got[-1]["response"]
    assert done["status"] == "completed"
    assert done["output"][0]["content"][0]["text"] == "Hello."
    assert done["usage"]["input_tokens"] == 120 and done["usage"]["total_tokens"] == 123


def test_a_tool_call_becomes_a_function_call_item_after_the_text():
    got = _run(_chunks(
        {"content": "Let me look."},
        {"tool_calls": [{"id": "abc", "type": "function",
                         "function": {"name": "exec_command", "arguments": "{\"cmd\": \"ls\"}"}}]}))
    kinds = [f["type"] for f in got]
    assert "response.output_item.done" in kinds
    items = got[-1]["response"]["output"]
    assert [i["type"] for i in items] == ["message", "function_call"]
    call = items[1]
    assert call["name"] == "exec_command" and call["call_id"] == "abc"
    assert call["arguments"] == "{\"cmd\": \"ls\"}" and call["status"] == "completed"
    # the message closed before the call opened, in order
    order = [(f["type"], f.get("output_index")) for f in got if "output_index" in f]
    assert order.index(("response.output_item.done", 0)) < order.index(("response.output_item.added", 1))


def test_thinking_is_kept_out_of_the_answer_even_across_chunks():
    got = _run(_chunks({"content": "<thi"}, {"content": "nk>let me reason"}, {"content": "…</th"},
                       {"content": "ink>The answer is 4."}))
    text = "".join(f["delta"] for f in got if f["type"] == "response.output_text.delta")
    assert text == "The answer is 4."


def test_an_upstream_failure_is_a_failed_response(monkeypatch):
    async def broken(base_url, request, timeout=600.0):
        raise RuntimeError("local server 500: boom")
        yield  # pragma: no cover

    monkeypatch.setattr(gateway, "chat_chunks", broken)

    async def go():
        return "".join([f async for f in gateway.respond("http://127.0.0.1:1/v1", "m",
                                                        {"model": "qwen", "input": "hi"})])
    got = frames_of(asyncio.run(go()))
    assert got[-1]["type"] == "response.failed" and "boom" in got[-1]["response"]["error"]["message"]


def test_the_whole_exchange_end_to_end(monkeypatch):
    async def fake(base_url, request, timeout=600.0):
        assert base_url.endswith("/v1") and request["messages"][-1]["content"] == "hi"
        for c in _chunks({"content": "hey"}):
            yield c

    monkeypatch.setattr(gateway, "chat_chunks", fake)

    async def go():
        return "".join([f async for f in gateway.respond("http://127.0.0.1:1/v1", "m",
                                                        {"model": "qwen", "input": "hi"})])
    got = frames_of(asyncio.run(go()))
    assert got[-1]["type"] == "response.completed"
    assert got[-1]["response"]["output"][0]["content"][0]["text"] == "hey"


# ---- the companions ----------------------------------------------------------------

def test_every_local_model_gets_a_codex_companion(tmp_path, monkeypatch):
    from eki import secrets, settings
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from eki.models import LocalModel
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    settings.save({"router_model": "tiny"})
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [
        BackendInfo(key="qwen", kind="mlx", label="Qwen 27B", capabilities=Capabilities(context_tokens=32000)),
        BackendInfo(key="tiny", kind="mlx", label="Qwen 2B", capabilities=Capabilities(context_tokens=32000)),
        BackendInfo(key="codex", kind="codex", label="Codex", cost=Cost(tier=50),
                    capabilities=Capabilities(repo=True, tools=True)),
    ]
    cfg.options = {"qwen": {"base_url": "http://127.0.0.1:1", "model": "mlx-community/Qwen3.8-27B-4bit"},
                   "tiny": {"base_url": "http://127.0.0.1:2", "model": "mlx-community/Qwen3.5-2B-MLX-4bit"},
                   "codex": {"binary": "/bin/echo"}}
    cfg.local_models = [LocalModel(key="qwen", label="Qwen", port=1, start="true", backend="qwen", gb=16),
                        LocalModel(key="tiny", label="Tiny", port=2, start="true", backend="tiny", gb=2)]
    eng = Engine(cfg, port=8799)
    keys = [b.key for b in eng.backends]
    assert "codex-qwen" in keys and "codex-tiny" not in keys      # the router model stays out
    companion = eng.get("codex-qwen")
    assert companion.info.cost.tier == 0 and companion.info.capabilities.repo
    assert companion.model == "qwen" and companion.options["gateway"] == "http://127.0.0.1:8799/v1"
    argv = " ".join(companion.options.get("gateway") and ["x"] or [])
    # its runs start the local model it drives
    assert eng._local_for("codex-qwen").key == "qwen"
    rec = eng.registry.get("codex-qwen", "")
    assert rec is not None and rec.klass == "large_open"
    asyncio.run(eng.quota.stop())
