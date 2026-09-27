"""The Responses adapter's two mappings, with no server at all."""
import json

import pytest

from eki import responses
from eki.responses_stream import Think, Translator


def events(frames):
    out = []
    for f in frames:
        head, data = f.decode().strip().split("\n", 1)
        assert head.startswith("event: ") and data.startswith("data: ")
        got = json.loads(data[len("data: "):])
        assert got["type"] == head[len("event: "):]
        out.append(got)
    return out


def run(chunks, **kw):
    t = Translator("m", **kw)
    frames = list(t.start())
    for c in chunks:
        frames += list(t.feed(c))
    frames += list(t.finish())
    return events(frames)


def text(s):
    return {"choices": [{"delta": {"content": s}}]}


def call(index, id=None, name=None, args=""):
    fn = {"arguments": args}
    if name:
        fn["name"] = name
    c = {"index": index, "function": fn}
    if id:
        c["id"] = id
    return {"choices": [{"delta": {"tool_calls": [c]}}]}


def test_request_maps_messages_tools_and_a_function_call_round_trip():
    body = {"model": "local", "instructions": "be brief", "stream": True,
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "sandbox: on"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]},
                {"type": "reasoning", "summary": []},
                {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"cmd":["ls"]}'},
                {"type": "function_call", "call_id": "c2", "name": "shell", "arguments": '{"cmd":["pwd"]}'},
                {"type": "function_call_output", "call_id": "c1", "output": "a.py"},
                {"type": "function_call_output", "call_id": "c2", "output": "/tmp"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a.py"}]}],
            "tools": [{"type": "function", "name": "shell", "description": "run",
                       "parameters": {"type": "object", "properties": {"cmd": {"type": "array"}}}},
                      {"type": "web_search"}]}
    got = responses.to_chat(body, "qwen")
    assert got["model"] == "qwen" and got["stream"] is True
    assert got["messages"] == [
        {"role": "system", "content": "be brief\n\nsandbox: on"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": '{"cmd":["ls"]}'}},
            {"id": "c2", "type": "function", "function": {"name": "shell", "arguments": '{"cmd":["pwd"]}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "a.py"},
        {"role": "tool", "tool_call_id": "c2", "content": "/tmp"},
        {"role": "assistant", "content": "a.py"}]
    assert got["tools"] == [{"type": "function", "function": {
        "name": "shell", "description": "run",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "array"}}}}}]
    assert got["tool_choice"] == "auto"


def test_a_string_input_and_freeform_tools():
    body = {"input": "hi", "tools": [{"type": "custom", "name": "apply_patch", "description": "patch"}]}
    got = responses.to_chat(body, "m")
    assert got["messages"] == [{"role": "user", "content": "hi"}]
    fn = got["tools"][0]["function"]
    assert fn["name"] == "apply_patch" and fn["parameters"]["required"] == ["input"]
    back = responses.to_chat({"input": [
        {"type": "custom_tool_call", "call_id": "p", "name": "apply_patch", "input": "*** Begin Patch"},
        {"type": "custom_tool_call_output", "call_id": "p", "output": "ok"}]}, "m")
    assert json.loads(back["messages"][0]["tool_calls"][0]["function"]["arguments"]) == {"input": "*** Begin Patch"}
    assert back["messages"][1] == {"role": "tool", "tool_call_id": "p", "content": "ok"}


def test_previous_response_id_is_refused():
    with pytest.raises(responses.BadRequest):
        responses.to_chat({"previous_response_id": "resp_1", "input": "hi"}, "m")


def test_a_text_answer_is_one_message():
    evs = run([text("Hel"), text("lo"), {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}])
    assert [e["type"] for e in evs] == [
        "response.created", "response.in_progress",
        "response.output_item.added", "response.content_part.added",
        "response.output_text.delta", "response.output_text.delta",
        "response.output_text.done", "response.content_part.done", "response.output_item.done",
        "response.completed"]
    assert [e["delta"] for e in evs if e["type"] == "response.output_text.delta"] == ["Hel", "lo"]
    done = evs[-1]["response"]
    assert done["status"] == "completed"
    assert done["output"][0]["content"][0]["text"] == "Hello" and done["output"][0]["role"] == "assistant"
    assert done["usage"]["input_tokens"] == 7 and done["usage"]["output_tokens"] == 2
    assert done["usage"]["total_tokens"] == 9
    assert [e["sequence_number"] for e in evs] == list(range(1, len(evs) + 1))


def test_tool_calls_are_items_with_argument_deltas():
    evs = run([text("Let me look."), call(0, "c1", "shell", '{"cmd":'), call(0, args='["ls"]}'),
               call(1, "c2", "shell", '{"cmd":["pwd"]}')])
    types = [e["type"] for e in evs]
    assert types == [
        "response.created", "response.in_progress",
        "response.output_item.added", "response.content_part.added", "response.output_text.delta",
        "response.output_text.done", "response.content_part.done", "response.output_item.done",
        "response.output_item.added", "response.function_call_arguments.delta",
        "response.function_call_arguments.delta",
        "response.output_item.added", "response.function_call_arguments.delta",
        "response.function_call_arguments.done", "response.output_item.done",
        "response.function_call_arguments.done", "response.output_item.done",
        "response.completed"]
    calls = [e["item"] for e in evs if e["type"] == "response.output_item.done" and e["item"]["type"] == "function_call"]
    assert [(c["call_id"], c["name"], c["arguments"], c["status"]) for c in calls] == [
        ("c1", "shell", '{"cmd":["ls"]}', "completed"), ("c2", "shell", '{"cmd":["pwd"]}', "completed")]
    assert [o["type"] for o in evs[-1]["response"]["output"]] == ["message", "function_call", "function_call"]
    assert evs[-1]["response"]["usage"]["total_tokens"] > 0       # estimated when the server doesn't count


def test_a_freeform_tool_call_comes_back_as_a_custom_item():
    evs = run([call(0, "p", "apply_patch", json.dumps({"input": "*** Begin Patch\n"}))], custom={"apply_patch"})
    item = [e["item"] for e in evs if e["type"] == "response.output_item.done"][0]
    assert item["type"] == "custom_tool_call" and item["input"] == "*** Begin Patch\n" and item["call_id"] == "p"


def test_think_is_stripped_across_chunks():
    pieces = ["<th", "ink>plan", "ning</th", "ink>", "\n\nThe ", "answer <", "b>ok</b> <thi", "nk>more</think>!"]
    evs = run([text(p) for p in pieces])
    shown = "".join(e["delta"] for e in evs if e["type"] == "response.output_text.delta")
    assert shown == "The answer <b>ok</b> !"
    assert evs[-1]["response"]["output"][0]["content"][0]["text"] == shown


def test_think_holds_only_what_could_be_a_tag():
    t = Think()
    assert t.feed("a <") == "a "
    assert t.feed("b") == "<b"
    assert t.feed("<think>never closed") == ""
    assert t.flush() == ""
    t2 = Think()
    assert t2.feed("x <thi") == "x "
    assert t2.flush() == "<thi"


def test_a_broken_stream_fails_the_response():
    t = Translator("m")
    evs = events(list(t.start()) + list(t.fail("gone")))
    assert evs[-1]["type"] == "response.failed" and evs[-1]["response"]["error"]["message"] == "gone"
