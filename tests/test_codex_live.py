# SPDX-License-Identifier: Apache-2.0
"""Codex kept open under eki, through a stand-in app-server."""
import asyncio
import sys
from pathlib import Path

import pytest

from eki import codex_live, live

FAKE = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]


def run(coro):
    return asyncio.run(coro)


async def _session(**kw):
    s = codex_live.CodexSession(FAKE, "/tmp", None, **kw)
    await s.start()
    return s


async def _collect(session, prompt, answer=None):
    await session.send(prompt)
    got = []
    async for ev in session.turn():
        got.append(ev)
        if ev["kind"] in ("ask", "permission") and answer is not None:
            await session.answer(ev["request_id"], answer(ev))
    return got


def test_the_handshake_starts_a_thread_with_the_permissions_asked_for():
    async def go():
        s = await _session(model="gpt-5.5", permissions="ask")
        try:
            assert s.session_id == "thread-1"
            assert s._thread_params()["approvalPolicy"] == "on-request"
            assert s._thread_params()["sandbox"] == "workspace-write"
            assert [c["name"] for c in s.commands][:3] == ["compact", "model", "new"]
        finally:
            await s.close()
        s = await _session(permissions="auto")
        # still on-request: "never" makes Codex refuse what its rules flag; eki says yes instead
        assert s._thread_params() == {"approvalPolicy": "on-request", "sandbox": "danger-full-access", "cwd": "/tmp"}
        await s.close()
    run(go())


def test_a_turn_streams_text_and_tool_lines_then_a_result():
    async def go():
        s = await _session(model="gpt-5.5")
        try:
            got = await _collect(s, "hello")
            kinds = [e["kind"] for e in got]
            assert kinds == ["activity", "activity", "text", "text", "context", "result"]
            assert live.summarize_activity(got[0]["tool"], got[0]["input"]) == "Running ls"
            assert live.summarize_activity(got[1]["tool"], got[1]["input"]) == "Editing a.py"
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Echo: hello (gpt-5.5)"
            assert got[-2] == {"kind": "context", "used": 100, "window": 131072}
            assert got[-1]["usage"] == {"input_tokens": 90, "output_tokens": 10,
                                        "context_used": 100, "context_window": 131072}
        finally:
            await s.close()
    run(go())


def test_a_question_is_answered_in_the_shape_the_cards_produce():
    async def go():
        s = await _session()
        try:
            def reply(ev):
                assert ev["questions"][0]["question"] == "Red or blue?"
                assert ev["questions"][0]["options"][1]["label"] == "Blue"
                return {"behavior": "allow",
                        "updatedInput": {**ev["input"], "answers": {"Red or blue?": "Blue"}}}
            got = await _collect(s, "please ask me", reply)
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "You chose Blue."
        finally:
            await s.close()
    run(go())


def test_a_command_approval_maps_allow_always_and_deny():
    async def go():
        s = await _session(permissions="ask")
        try:
            got = await _collect(s, "run it", lambda ev: {"behavior": "deny", "message": "no"})
            perm = next(e for e in got if e["kind"] == "permission")
            assert perm["tool"] == "Bash" and perm["input"]["command"] == "rm -rf build"
            assert perm["description"] == "cleanup"
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Understood, I won't."
            got = await _collect(s, "run it", lambda ev: {"behavior": "allow", "updatedInput": ev["input"],
                                                          "updatedPermissions": ev["suggestions"]})
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Removed build (acceptForSession)."
        finally:
            await s.close()
    run(go())


def test_in_auto_mode_eki_approves_and_says_so():
    async def go():
        s = await _session(permissions="auto")
        try:
            got = await _collect(s, "run it")               # no answer callback: none needed
            kinds = [e["kind"] for e in got]
            assert "permission" not in kinds
            approved = next(e for e in got if e["kind"] == "activity" and e["tool"] == "approved")
            assert live.summarize_activity(approved["tool"], approved["input"]) == "Approved rm -rf build"
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Removed build (accept)."
        finally:
            await s.close()
    run(go())


def test_ekis_slash_commands_for_codex():
    async def go():
        s = await _session(model="gpt-5.5")
        try:
            got = await _collect(s, "/compact")
            assert got[0] == {"kind": "note", "text": "Context compacted"} and got[-1]["kind"] == "result"
            got = await _collect(s, "/model gpt-5.5-mini")
            assert "gpt-5.5-mini" in got[0]["text"]
            got = await _collect(s, "hi")
            assert "(gpt-5.5-mini)" in "".join(e["text"] for e in got if e["kind"] == "text")
            got = await _collect(s, "/status")
            assert "gpt-5.5-mini" in got[0]["text"] and "Tokens" in got[0]["text"]
            first = s.session_id
            got = await _collect(s, "/new")
            assert s.session_id != first and "context cleared" in got[0]["text"]
            # an unknown slash command goes to the model as words
            got = await _collect(s, "/whatever")
            assert "Echo: /whatever" in "".join(e["text"] for e in got if e["kind"] == "text")
        finally:
            await s.close()
    run(go())


def test_a_thread_that_cannot_resume_says_so():
    async def go():
        s = codex_live.CodexSession(FAKE, "/tmp", None, resume="gone")
        with pytest.raises(RuntimeError) as e:
            await s.start()
        assert "no such thread" in str(e.value)
    run(go())


def test_plain_commands():
    assert codex_live._plain_command("/bin/zsh -lc 'ls -la'") == "ls -la"
    assert codex_live._plain_command(["python", "-m", "pytest"]) == "python -m pytest"
    assert codex_live._plain_command("ls") == "ls"


# ---- through the engine ------------------------------------------------------------

def test_a_codex_run_goes_through_the_open_session(tmp_path, monkeypatch):
    from eki import secrets, settings
    from eki.adapters import codex as cx
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="codex", kind="codex", label="Codex", cost=Cost(tier=50),
                                capabilities=Capabilities(context_tokens=200000, repo=True, tools=True))]
    cfg.options = {"codex": {"binary": FAKE[0]}}
    monkeypatch.setattr(cx.CodexBackend, "live_argv", lambda self: FAKE)
    eng = Engine(cfg)

    async def go():
        started = await eng.ask("please ask me", backend_key="codex")
        rid, cid = started["run"], started["conversation"]
        q = eng.runner.subscribe(rid)
        events = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            events.append(ev)
            if ev["event"] == "ask":
                await eng.answer(rid, ev["request_id"], {"behavior": "allow",
                                 "updatedInput": {**ev["input"], "answers": {"Red or blue?": "Red"}}})
            if ev["event"] == "state" and ev["state"] in ("done", "failed", "cancelled"):
                break
        assert events[-1]["state"] == "done"
        assert "".join(e.get("text", "") for e in events if e["event"] == "output") == "You chose Red."
        assert eng.store.session(cid, "codex") == "thread-1"
        assert [c["name"] for c in await eng.commands_for(cid)][0] == "compact"
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_a_local_model_that_stops_to_announce_is_nudged(tmp_path, monkeypatch):
    """The fake echoes what it's told; a companion's first turn that only
    announces gets one "go ahead" and the thread shows the nudge."""
    from eki import secrets, settings
    from eki.adapters import codex as cx
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="codex-qwen", kind="codex", label="Codex on Qwen", cost=Cost(tier=0),
                                capabilities=Capabilities(context_tokens=32000, repo=True, tools=True))]
    cfg.options = {"codex-qwen": {"binary": FAKE[0], "local_model": "qwen", "model": "qwen"}}
    monkeypatch.setattr(cx.CodexBackend, "live_argv", lambda self: FAKE + ["--announce-only"])
    eng = Engine(cfg)

    async def go():
        started = await eng.ask("Let me first check the folder", backend_key="codex-qwen")
        rid = started["run"]
        q = eng.runner.subscribe(rid)
        events = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            events.append(ev)
            if ev["event"] == "state" and ev["state"] in ("done", "failed", "cancelled"):
                break
        text = "".join(e.get("text", "") for e in events if e["event"] == "output")
        lines = [e["text"] for e in events if e["event"] == "activity"]
        assert "Nudged to carry on" in lines
        assert "Echo: Go ahead" in text            # the second turn happened
        session = eng.live[started["conversation"]]
        assert "same turn" in session.developer_instructions
        await eng.quota.stop()
        await eng.close()
    run(go())
