# SPDX-License-Identifier: Apache-2.0
"""Claude Code kept open under eki: the protocol, and the turn events."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from eki import live

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def run(coro):
    return asyncio.run(coro)


async def _session(*extra):
    s = live.LiveSession(FAKE + list(extra), None, None, append_system_prompt="Be terse.")
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


def test_the_handshake_lists_commands_minus_the_terminal_only_ones():
    async def go():
        s = await _session("--session-id", "abc")
        try:
            assert s.session_id == "abc" and s.model == "claude-sonnet-5"
            assert [c["name"] for c in s.commands] == ["compact", "review"]
        finally:
            await s.close()
    run(go())


def test_a_turn_streams_text_and_tool_lines_then_a_result():
    async def go():
        s = await _session()
        try:
            got = await _collect(s, "hello")
            kinds = [e["kind"] for e in got]
            assert kinds == ["activity", "text", "text", "context", "result"]
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Echo: hello (claude-sonnet-5)"
            assert live.summarize_activity(got[0]["tool"], got[0]["input"]) == "Reading README.md"
            assert got[-1]["usage"]["output_tokens"] > 0 and not s.busy
            # every assistant message says how full the thread is
            assert got[-2]["used"] > 12000 and got[-2]["window"] == 1_000_000   # a sonnet: known before the result
        finally:
            await s.close()
    run(go())


def test_a_question_waits_for_the_answer_and_carries_on():
    async def go():
        s = await _session()
        try:
            def reply(ev):
                assert ev["questions"][0]["question"] == "Red or blue?"
                return {"behavior": "allow",
                        "updatedInput": {**ev["input"], "answers": {"Red or blue?": "Blue"}}}
            got = await _collect(s, "please ask me", reply)
            assert [e["kind"] for e in got][:2] == ["activity", "ask"]
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "You chose Blue."
            assert s.pending() == []
        finally:
            await s.close()
    run(go())


def test_a_permission_prompt_can_be_denied():
    async def go():
        s = await _session()
        try:
            got = await _collect(s, "run it", lambda ev: {"behavior": "deny", "message": "no"})
            perm = next(e for e in got if e["kind"] == "permission")
            assert perm["tool"] == "Bash" and perm["input"]["command"] == "rm -rf build"
            assert perm["suggestions"]
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Understood, I won't."
        finally:
            await s.close()
    run(go())


def test_slash_commands_are_plain_messages_and_notes_come_back():
    async def go():
        s = await _session()
        try:
            got = await _collect(s, "/compact")
            assert got[0] == {"kind": "note", "text": "Context compacted (manual)"}
            assert got[-1]["kind"] == "result"
        finally:
            await s.close()
    run(go())


def test_the_model_can_change_mid_session():
    async def go():
        s = await _session()
        try:
            await s.set_model("claude-opus-5")
            got = await _collect(s, "hi")
            assert "(claude-opus-5)" in "".join(e["text"] for e in got if e["kind"] == "text")
        finally:
            await s.close()
    run(go())


def test_a_session_that_cannot_resume_says_so():
    async def go():
        s = live.LiveSession(FAKE + ["--resume", "gone"], None, None)
        with pytest.raises(RuntimeError) as e:
            await s.start()
        assert "No conversation found" in str(e.value)
    run(go())


def test_activity_lines_read_like_the_terminal():
    f = live.summarize_activity
    assert f("Bash", {"command": "pytest -q"}) == "Running pytest -q"
    assert f("Edit", {"file_path": "/a/b/c.py"}) == "Editing c.py"
    assert f("Grep", {"pattern": "TODO"}) == "Searching for TODO"
    assert f("error", {"text": "boom"}) == "⚠ boom"
    assert f("Whatever", {}) == "Whatever"


# ---- through the engine: a run with cards, answered from the outside -------

def test_a_claude_run_streams_asks_and_records_the_turn(tmp_path, monkeypatch):
    from eki import settings
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from tests.test_providers import fake_keychain  # noqa: F401 (fixture import for monkeypatching)

    vault = {}
    from eki import secrets
    monkeypatch.setattr(secrets, "get", lambda k: vault.get(k))
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="claude", kind="claude_code", label="Claude",
                                capabilities=Capabilities(context_tokens=200000, repo=True, tools=True),
                                cost=Cost(tier=50))]
    # the fake program stands in for the binary; `binary` may be a path
    cfg.options = {"claude": {"binary": FAKE[0]}}
    eng = Engine(cfg)
    # hand the fake script in through the argv the adapter builds
    from eki.adapters import claude_code as cc
    real = cc.ClaudeCodeBackend.live_argv

    def argv(self, cwd, resume, sid):
        out = real(self, cwd, resume, sid)
        return [out[0], FAKE[1]] + out[1:]
    monkeypatch.setattr(cc.ClaudeCodeBackend, "live_argv", argv)
    monkeypatch.setattr(cc.ClaudeCodeBackend, "_no_bare_flag", lambda self: None)

    async def go():
        started = await eng.ask("please ask me")
        rid, cid = started["run"], started["conversation"]
        q = eng.runner.subscribe(rid)
        events = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            events.append(ev)
            if ev["event"] == "ask":
                assert eng.pending[rid]["request_id"] == ev["request_id"]
                ok = await eng.answer(rid, ev["request_id"],
                                      {"behavior": "allow",
                                       "updatedInput": {**ev["input"], "answers": {"Red or blue?": "Red"}}})
                assert ok and rid not in eng.pending
            if ev["event"] == "state" and ev["state"] in ("done", "failed", "cancelled"):
                break
        kinds = [e["event"] for e in events]
        assert "ask" in kinds and kinds[-1] == "state" and events[-1]["state"] == "done"
        text = "".join(e.get("text", "") for e in events if e["event"] == "output")
        assert text == "You chose Red."
        turns = eng.store.turns(cid)
        assert turns[-1]["role"] == "assistant" and turns[-1]["content"] == "You chose Red."
        assert eng.store.session(cid, "claude")                     # resumable later
        assert [c["name"] for c in await eng.commands_for(cid)] == ["compact", "review"]
        # the session stays open for the next turn, and idles out later
        assert cid in eng.live and eng.live[cid].alive
        assert await eng.reap_live(now=eng.live[cid].last_used + 10) == 0
        assert await eng.reap_live(now=eng.live[cid].last_used + 3 * 3600) == 1
        await eng.quota.stop()
        await eng.close()

    run(go())


def test_a_session_opened_for_the_command_list_is_adopted_by_the_first_run(tmp_path, monkeypatch):
    from eki import secrets, settings
    from eki.adapters import claude_code as cc
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="claude", kind="claude_code", label="Claude",
                                capabilities=Capabilities(context_tokens=200000), cost=Cost(tier=50))]
    cfg.options = {"claude": {"binary": FAKE[0]}}
    real = cc.ClaudeCodeBackend.live_argv
    monkeypatch.setattr(cc.ClaudeCodeBackend, "live_argv",
                        lambda self, cwd, resume, sid: [real(self, cwd, resume, sid)[0], FAKE[1]]
                        + real(self, cwd, resume, sid)[1:])
    monkeypatch.setattr(cc.ClaudeCodeBackend, "_no_bare_flag", lambda self: None)
    eng = Engine(cfg)

    async def go():
        names = [c["name"] for c in await eng.commands_for("", "")]
        assert names == ["compact", "review"] and "" in eng.warm
        warm = eng.warm[""]
        started = await eng.ask("hello")
        q = eng.runner.subscribe(started["run"])
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        assert eng.live[started["conversation"]] is warm and not eng.warm
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_permissions_setting_shapes_both_programs_argv(tmp_path, monkeypatch):
    from eki import settings
    from eki.adapters.base import BackendInfo
    from eki.adapters.claude_code import ClaudeCodeBackend
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ClaudeCodeBackend, "_no_bare_flag", lambda self: None)
    b = ClaudeCodeBackend(BackendInfo(key="claude", kind="claude_code", label="c"), {"binary": "/bin/echo"})
    # the default: run without asking, in both modes
    live = b.live_argv("/tmp/p", None, "sid")
    assert "--permission-mode" in live and live[live.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allow-dangerously-skip-permissions" in live and "--permission-prompt-tool" in live
    assert "--dangerously-skip-permissions" in b._argv("hi", None, "/tmp/p")
    # ask me each time: the prompt tool carries every permission as a card
    settings.save({"permissions": "ask"})
    live = b.live_argv("/tmp/p", None, "sid")
    assert live[live.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allow-dangerously-skip-permissions" not in live
    assert "--dangerously-skip-permissions" not in b._argv("hi", None, "/tmp/p")
    # a chat turn with no folder asks in its default mode
    assert "--permission-mode" not in b.live_argv(None, None, "sid")


def test_a_turn_that_only_announces_sounds_unfinished():
    assert live.sounds_unfinished("On it. First let me fix the page style, then start both.")
    assert live.sounds_unfinished("I'll install the dependencies now.")
    assert not live.sounds_unfinished("Done. Renamed add to sum_two and updated the caller.")
    # what happened tonight: work was done, then a sentence of work in progress, then silence
    assert live.sounds_unfinished("Typecheck passes. Now starting the servers.\n\nThe API is up. "
                                  "The web platform needs a few extra packages — installing them.")
    assert not live.sounds_unfinished("The API is running on port 8000 and Expo is up. Done.")
    assert not live.sounds_unfinished("Should I also update the tests?")
    assert not live.sounds_unfinished("")


def test_the_meter_knows_a_family_window_before_the_first_result():
    assert live.known_window("claude-fable-5-1") == 1_000_000
    assert live.known_window("claude-haiku-4-5") == 200_000
    assert live.known_window("something-else") == 0


def test_a_turn_the_program_took_by_itself_becomes_a_turn_in_the_thread(tmp_path, monkeypatch):
    """Claude Code carries on when a background task finishes. Its events
    arrive while nobody asked; eki writes them down as their own turn and
    never hands them to the next question as a stale reply."""
    from eki import secrets, settings
    from eki.adapters import claude_code as cc
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="claude_code", kind="claude_code", label="Claude Code", cost=Cost(tier=50),
                                capabilities=Capabilities(context_tokens=200000, repo=True, tools=True))]
    cfg.options = {"claude_code": {"binary": FAKE[0]}}
    monkeypatch.setattr(cc.ClaudeCodeBackend, "live_argv", lambda self, cwd=None, resume=None, session_id=None: FAKE)
    eng = Engine(cfg)

    async def go():
        started = await eng.ask("start a background download", backend_key="claude_code")
        rid, cid = started["run"], started["conversation"]
        q = eng.runner.subscribe(rid)
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        # the program carried on; the follower notices and writes it down
        for _ in range(30):
            await asyncio.sleep(0.1)
            if await eng.follow_live():
                break
        else:
            raise AssertionError("the program's own turn was never followed")
        for _ in range(50):
            await asyncio.sleep(0.1)
            if not eng.runner.running:
                break
        turns = eng.store.turns(cid)
        assert [t["role"] for t in turns] == ["user", "assistant", "assistant"]
        assert "background" in turns[1]["content"]
        assert turns[2]["content"].startswith("The download finished")
        assert turns[2]["reason"] == "carried on by itself"
        assert json.loads(turns[2]["meta"])["continued"] is True
        # and the next question gets its own answer, not the stale one
        again = await eng.ask("hello", conversation=cid, backend_key="claude_code")
        q = eng.runner.subscribe(again["run"])
        text = ""
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["event"] == "output":
                text += ev.get("text", "")
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        assert text.startswith("Echo: hello")
        await eng.quota.stop()
        await eng.close()
    run(go())
