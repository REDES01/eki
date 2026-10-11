"""A Claude turn ends only at its real final result — never while a tool call waits."""
import json
import stat

from eki.providers.base import Outcome, Turn
from eki.providers.claude_code import ClaudeCode

from tests.test_providers import FakeChannel

INIT = {"type": "system", "subtype": "init", "session_id": "s1", "model": "opus"}
BASH = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sed -i '' 1d log.md"}}]}}
NOTIFIED = {"type": "result", "subtype": "success", "is_error": False,
            "result": "background task finished", "usage": {"output_tokens": 2}}
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
FINAL_TEXT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ITEM: done"}]}}
FINAL = {"type": "result", "subtype": "success", "is_error": False, "usage": {"output_tokens": 9}}


def test_result_from_a_background_task_does_not_end_the_turn():
    p, ch, out, got = ClaudeCode("claude", {}), FakeChannel(), Outcome(), []
    emit = lambda k, d: got.append((k, d))
    for event in [INIT, {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "keep both, head's first"}]}}, BASH, NOTIFIED]:
        p.read(event, emit, out, ch)
    assert not out.finished and out.error == ""
    assert ("usage", {"usage": {"output_tokens": 2}, "cost": None}) in got   # still counted
    for event in [TOOL_RESULT, FINAL_TEXT]:
        p.read(event, emit, out, ch)
    assert not out.finished
    p.read(FINAL, emit, out, ch)
    assert out.finished and out.error == ""
    assert "ITEM: done" in [d["text"] for k, d in got if k == "text"]


def test_plain_stream_still_finishes():
    p, ch, out = ClaudeCode("claude", {}), FakeChannel(), Outcome()
    for event in [INIT, FINAL_TEXT, FINAL]:
        p.read(event, lambda k, d: None, out, ch)
    assert out.finished and out.error == ""


def fake_claude(tmp_path, events):
    script = tmp_path / "claude"
    body = "\n".join(f"print({json.dumps(json.dumps(e))}, flush=True)" for e in events)
    script.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.stdin.readline()\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return ClaudeCode("claude", {"binary": str(script)})


def test_stream_ending_mid_tool_call_fails(tmp_path):
    p = fake_claude(tmp_path, [INIT, BASH])
    out = p.take(Turn(prompt="resolve", history=[], cwd=str(tmp_path)), lambda k, d: None)
    assert out.state == "failed"
    assert "cut off mid tool call" in out.error and "Bash" in out.error


def test_stream_through_take_ends_at_the_real_result(tmp_path):
    p = fake_claude(tmp_path, [INIT, BASH, NOTIFIED, TOOL_RESULT, FINAL_TEXT, FINAL])
    got = []
    out = p.take(Turn(prompt="resolve", history=[], cwd=str(tmp_path)), lambda k, d: got.append((k, d)))
    assert out.state == "done" and out.finished
    assert "ITEM: done" in [d["text"] for k, d in got if k == "text"]
