"""One call, no tools, through Claude Code and Codex — with fake programs, never the real ones."""
import json
import os
import sys
import threading

import pytest

from eki.providers import oneshot
from eki.providers.base import Turn
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex
from eki.providers.local import Local

FAKE = """#!{py}
import json, os, sys
stdin = sys.stdin.read()
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "files": os.listdir("."), "stdin": stdin}},
          open({record!r}, "w"))
if {sleep}:
    import time; time.sleep({sleep})
sys.stdout.write({out!r})
sys.stderr.write({err!r})
sys.exit({code})
"""


def fake(tmp_path, out="", err="", code=0, sleep=0, name="prog"):
    record = tmp_path / f"{name}.json"
    path = tmp_path / name
    path.write_text(FAKE.format(py=sys.executable, record=str(record), out=out, err=err, code=code, sleep=sleep))
    path.chmod(0o755)
    return str(path), record


def said(record):
    return json.loads(record.read_text())


# ---- argv --------------------------------------------------------------------------------

def test_claude_argv_exact():
    assert oneshot.claude_argv("claude", None, "be brief") == [
        "claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
        "--no-session-persistence", "--system-prompt", "be brief"]
    assert oneshot.claude_argv("claude", "claude-haiku-4-5", "s")[-2:] == ["--model", "claude-haiku-4-5"]


def test_codex_argv_exact():
    assert oneshot.codex_argv("codex", None, 'say "hi"\nnow') == [
        "codex", "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
        "-c", 'developer_instructions="say \\"hi\\"\\nnow"', "-"]
    argv = oneshot.codex_argv("codex", "gpt-5-mini", "s")
    assert argv[-3:] == ["-m", "gpt-5-mini", "-"]


# ---- parsing -----------------------------------------------------------------------------

def test_claude_result():
    assert oneshot.claude_result(json.dumps({"type": "result", "is_error": False, "result": "4"})) == "4"
    with pytest.raises(RuntimeError, match="broken"):
        oneshot.claude_result(json.dumps({"type": "result", "is_error": True, "result": "broken"}))
    with pytest.raises(oneshot.Limited):
        oneshot.claude_result(json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1760000000"}))


def test_codex_result_last_agent_message():
    lines = [{"type": "thread.started", "thread_id": "t"},
             {"type": "item.completed", "item": {"id": "0", "type": "agent_message", "text": "first"}},
             {"type": "item.completed", "item": {"id": "1", "type": "reasoning", "text": "hmm"}},
             {"type": "item.completed", "item": {"id": "2", "type": "agent_message", "text": "last"}},
             {"type": "turn.completed", "usage": {}}]
    out = "banner\n" + "\n".join(json.dumps(x) for x in lines) + "\n"
    assert oneshot.codex_result(out) == "last"
    with pytest.raises(RuntimeError, match="no answer"):
        oneshot.codex_result(json.dumps({"type": "turn.completed"}))
    with pytest.raises(oneshot.Limited):
        oneshot.codex_result(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit"}}))


def test_fold_system_apart_rest_in_stdin():
    assert oneshot.fold([{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}]) == ("S", "Q")
    system, text = oneshot.fold([{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
                                 {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}])
    assert system == "S" and text == "User: a\n\nAssistant: b\n\nUser: c"


# ---- running -----------------------------------------------------------------------------

def test_run_failures(tmp_path):
    bin, _ = fake(tmp_path, err="boom", code=2)
    with pytest.raises(RuntimeError, match="boom"):
        oneshot.run([bin], "x", 10)
    bin, _ = fake(tmp_path, err="Error: rate limit exceeded", code=1, name="lim")
    with pytest.raises(oneshot.Limited):
        oneshot.run([bin], "x", 10)
    bin, _ = fake(tmp_path, out=json.dumps({"is_error": True, "result": "usage limit reached"}), code=1, name="lim2")
    with pytest.raises(oneshot.Limited):
        oneshot.run([bin], "x", 10)


def test_run_timeout_kills(tmp_path):
    bin, record = fake(tmp_path, sleep=30)
    with pytest.raises(TimeoutError):
        oneshot.run([bin], "x", 0.5)


def test_run_stop_gives_nothing(tmp_path):
    bin, _ = fake(tmp_path, sleep=30)
    stop = threading.Event()
    stop.set()
    assert oneshot.run([bin], "x", 10, stop=stop) == ""


def test_claude_complete_through_the_cli(tmp_path):
    bin, record = fake(tmp_path, out=json.dumps({"type": "result", "is_error": False, "result": "hello"}))
    p = ClaudeCode("claude", {"binary": bin})
    got = p.complete([{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}], model="haiku")
    assert got == {"text": "hello", "tool_calls": []}
    seen = said(record)
    assert seen["argv"] == oneshot.claude_argv(bin, "haiku", "SYS")[1:]
    assert seen["stdin"] == "hi" and seen["files"] == []
    assert not os.path.exists(seen["cwd"])          # the empty folder is gone afterwards


def test_codex_complete_in_an_empty_folder(tmp_path):
    out = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
    bin, record = fake(tmp_path, out=out)
    got = Codex("codex", {"binary": bin}).complete([{"role": "system", "content": "SYS"},
                                                    {"role": "user", "content": "q"}])
    assert got == {"text": "ok", "tool_calls": []}
    seen = said(record)
    assert seen["argv"] == oneshot.codex_argv(bin, None, "SYS")[1:]
    assert "-m" not in seen["argv"]
    assert seen["stdin"] == "q" and seen["files"] == []
    assert os.path.basename(seen["cwd"]).startswith("eki-oneshot-") and not os.path.exists(seen["cwd"])


# ---- chore runs --------------------------------------------------------------------------

def chore_turn(model=None):
    extra = {"row": "chore"}
    if model:
        extra["model"] = model
    return Turn(prompt="sum it up", history=[], extra=extra)


@pytest.mark.parametrize("cls,out", [
    (ClaudeCode, json.dumps({"type": "result", "is_error": False, "result": "the digest"})),
    (Codex, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "the digest"}}))])
def test_chore_take_is_one_text_event(tmp_path, cls, out):
    bin, record = fake(tmp_path, out=out)
    events = []
    got = cls("p", {"binary": bin}).take(chore_turn("m1"), lambda k, d: events.append((k, d)))
    assert got.state == "done"
    assert events == [("text", {"text": "the digest"})]     # no tools, no session
    argv = said(record)["argv"]
    assert "m1" in argv and "app-server" not in argv and "stream-json" not in argv


@pytest.mark.parametrize("cls", [ClaudeCode, Codex])
def test_chore_take_limit_is_limited(tmp_path, cls):
    bin, _ = fake(tmp_path, err="usage limit reached", code=1)
    got = cls("p", {"binary": bin}).take(chore_turn(), lambda k, d: None)
    assert got.state == "limited"


@pytest.mark.parametrize("cls", [ClaudeCode, Codex])
def test_chore_take_failure_is_failed(tmp_path, cls):
    bin, _ = fake(tmp_path, err="no such model", code=1)
    got = cls("p", {"binary": bin}).take(chore_turn(), lambda k, d: None)
    assert got.state == "failed" and "no such model" in got.error


# ---- local -------------------------------------------------------------------------------

class _Resp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


def test_local_complete_model_over_configured(monkeypatch):
    sent = []

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Resp([b'data: {"choices":[{"delta":{"content":"hi"}}]}\n', b"data: [DONE]\n"])

    monkeypatch.setattr("eki.providers.local.urllib.request.urlopen", urlopen)
    p = Local("local", {"model": "qwen"})
    assert p.complete([{"role": "user", "content": "x"}], model="x")["text"] == "hi"
    assert p.complete([{"role": "user", "content": "x"}])["text"] == "hi"
    assert [b["model"] for b in sent] == ["x", "qwen"]


def test_a_slow_reader_still_gets_a_prompt_bigger_than_the_pipe(tmp_path):
    """A program that starts reading only after a while (a busy Mac) must still get all of a
    prompt larger than the pipe's buffer, and see it end."""
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 1\nwc -c\n")
    slow.chmod(0o755)
    from eki.providers import oneshot
    big = "x" * 200_000
    out = oneshot.run([str(slow)], big, timeout=20)
    assert out.strip() == "200000"
