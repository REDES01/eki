# SPDX-License-Identifier: Apache-2.0
"""Gemini CLI as a backend: its headless stream read, its session kept, and
the same skill store and engine treatment as the other agent programs."""
import asyncio
import json
import sys

import pytest

from eki import adapters, catalog, priors, settings
from eki.adapters.base import BackendError, BackendInfo, Capabilities, Message
from eki.adapters.gemini_cli import read_event

EVENTS = [
    {"type": "init", "session_id": "sess-1", "model": "gemini-3-pro"},
    {"type": "message", "role": "user", "content": "hi"},
    {"type": "message", "role": "assistant", "content": "Hel", "delta": True},
    {"type": "tool_use", "tool_name": "read_file", "tool_id": "t1", "parameters": {}},
    {"type": "tool_result", "tool_id": "t1", "status": "success", "output": "…"},
    {"type": "message", "role": "assistant", "content": "lo", "delta": True},
    {"type": "result", "status": "success",
     "stats": {"total_tokens": 30, "input_tokens": 20, "output_tokens": 10}},
]


def _fake(tmp_path, events, code=0, stderr=""):
    """A `gemini` that records its argv and prints the given events."""
    script = tmp_path / "gemini"
    log = tmp_path / "argv.json"
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"if sys.argv[1:] == ['--version']:\n    print('0.40.0'); sys.exit(0)\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('Loaded cached credentials.')\n"
        f"for e in {events!r}:\n    print(json.dumps(e))\n"
        f"sys.stderr.write({stderr!r})\nsys.exit({code})\n")
    script.chmod(0o755)
    return str(script), log


def _backend(binary, **options):
    info = BackendInfo(key="gemini", kind="gemini_cli", label="Gemini CLI",
                       capabilities=Capabilities(tools=True, repo=True))
    return adapters.build(info, {"binary": binary, **options})


async def _collect(backend, **kw):
    return "".join([c async for c in backend.stream([Message("user", "hi")], **kw)])


def test_registered_and_classed_like_the_other_agent_programs():
    assert "gemini_cli" in adapters.kinds()
    assert "gemini_cli" in adapters.AGENT_CLIS
    assert priors.class_of("gemini_cli", {}) == "frontier_agent"
    assert priors.class_of_model("gemini_cli", "gemini-3-flash", {}) == "frontier_agent_fast"
    t = catalog.template("gemini_cli")
    assert t["needs"] == "binary" and t["binary"] == "gemini"


def test_streams_the_answer_and_keeps_the_session(tmp_path):
    binary, log = _fake(tmp_path, EVENTS)
    b = _backend(binary, model="gemini-3-pro")
    assert asyncio.run(b.health()).ok
    assert asyncio.run(_collect(b)) == "Hello"
    assert b.last_session == "sess-1"
    assert b.last_usage == {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
    argv = json.loads(log.read_text())
    assert argv[:4] == ["-p", "hi", "--output-format", "stream-json"]
    assert argv[argv.index("-m") + 1] == "gemini-3-pro"


def test_resume_and_permissions(tmp_path):
    binary, log = _fake(tmp_path, EVENTS)
    b = _backend(binary)
    settings.save({**settings.load(), "permissions": "auto"})
    asyncio.run(_collect(b, resume="sess-1"))
    argv = json.loads(log.read_text())
    assert argv[argv.index("--resume") + 1] == "sess-1"
    assert argv[argv.index("--approval-mode") + 1] == "yolo"
    settings.save({**settings.load(), "permissions": "ask"})
    asyncio.run(_collect(b))
    assert "--approval-mode" not in json.loads(log.read_text())       # a chat turn: read-only
    asyncio.run(_collect(b, cwd=str(tmp_path)))
    argv = json.loads(log.read_text())
    assert argv[argv.index("--approval-mode") + 1] == "auto_edit"


def test_a_failed_result_is_an_error(tmp_path):
    binary, _ = _fake(tmp_path, [
        {"type": "init", "session_id": "s"},
        {"type": "result", "status": "error", "error": {"message": "Quota exceeded"}}], code=1)
    with pytest.raises(BackendError, match="Quota exceeded"):
        asyncio.run(_collect(_backend(binary)))


def test_missing_binary_says_so(tmp_path):
    b = _backend(str(tmp_path / "nowhere"))
    assert not asyncio.run(b.health()).ok
    with pytest.raises(BackendError, match="gemini not found"):
        asyncio.run(_collect(b))


def test_unknown_events_and_warnings_are_noise():
    assert read_event({"type": "something_new", "content": "x"}) == ("", "")
    assert read_event({"type": "error", "severity": "warning", "message": "slow"}) == ("", "")
    assert read_event({"type": "message", "role": "assistant",
                       "content": [{"text": "a"}, {"text": "b"}]}) == ("ab", "")


def test_a_program_with_no_quota_reading_waits_behind_the_measured_ones(monkeypatch):
    """Gemini CLI has no quota eki reads; Claude Code and Codex still sort by
    their room, and it comes after them rather than scrambling the order."""
    from types import SimpleNamespace as NS
    from eki import engine as engine_mod

    def sub(key, kind, quota):
        return NS(key=key, info=NS(kind=kind, quota_source=quota))
    subs = [sub("gemini", "gemini_cli", None), sub("claude", "claude_code", "claude"),
            sub("codex", "codex", "codex")]
    room = {"claude": 2.0, "codex": 5.0}
    monkeypatch.setattr(engine_mod.capacity_mod, "room",
                        lambda data, source, windows: {"per_hour": room[source]})
    fake = NS(backends=subs, options={}, policy=NS(is_disabled=lambda k: False, rank=lambda k: 0),
              quota=NS(pace=lambda: {}, latest={"claude": NS(windows=[]), "codex": NS(windows=[])}))
    order = engine_mod.Engine._subscriptions(fake)
    assert [b.key for b in order] == ["codex", "claude", "gemini"]
