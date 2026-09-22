# SPDX-License-Identifier: Apache-2.0
"""Everything the terminal's panels ask Claude Code, asked by eki instead:
the handshake's account and models, the MCP panel, permission mode, usage,
context, rewind — and eki's own tools served to the program in-process."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from eki import live, mcpbridge, mcpregistry

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def run(coro):
    return asyncio.run(coro)


async def _session(bridge=None):
    s = live.LiveSession(FAKE, None, None, bridge=bridge)
    await s.start()
    return s


def test_the_handshake_says_who_is_logged_in_and_which_models_there_are():
    async def go():
        s = await _session()
        try:
            assert s.account["email"] == "x@example.com" and s.account["subscriptionType"] == "pro"
            assert [m["value"] for m in s.models] == ["sonnet", "fable"]
            assert s.version == "9.9.9" and s.tools == ["Bash", "Read"]
            assert [x["name"] for x in s.mcp_servers] == ["docs", "gmail"]
        finally:
            await s.close()
    run(go())


def test_the_mcp_panel_is_drawn_from_mcp_status_and_auth_goes_through_the_program():
    async def go():
        s = await _session()
        try:
            servers = await s.mcp_status()
            assert {x["name"]: x["status"] for x in servers} == {"docs": "connected", "gmail": "needs-auth"}
            reply = await s.mcp_authenticate("gmail")
            assert reply["opened"].startswith("https://")
            servers = await s.mcp_status()
            assert {x["name"]: x["status"] for x in servers}["gmail"] == "connected"
            await s.mcp_toggle("docs", False)
            with pytest.raises(RuntimeError):          # the program refuses; the engine op heals it
                await s.mcp_reconnect("docs")
            await s.mcp_toggle("docs", True)
            await s.mcp_reconnect("docs")
        finally:
            await s.close()
    run(go())


def test_the_other_panels_permission_mode_usage_context_rules_rewind():
    async def go():
        s = await _session()
        try:
            await s.set_permission_mode("plan")
            assert s.permission_mode == "plan"
            assert (await s.usage())["rate_limits"]["five_hour"]["utilization"] == 0.3
            ctx = await s.context_usage()
            assert ctx["totalTokens"] == 12000 and ctx["categories"][0]["name"] == "System prompt"
            assert (await s.permission_rules())["state"]["rules"][0]["toolName"] == "Bash"
            assert (await s.list_models())[1]["value"] == "fable"
            assert (await s.rewind_files("u-7", dry_run=True)) == {"rewound": "unknown", "dry_run": True}
            with pytest.raises(RuntimeError):
                await s.control({"subtype": "no_such_thing"})
        finally:
            await s.close()
    run(go())


def test_a_turn_carries_its_checkpoint_thinking_and_an_elicitation_card():
    async def go():
        s = await _session()
        try:
            await s.send("think")
            kinds = [ev["kind"] async for ev in s.turn()]
            assert kinds[:2] == ["checkpoint", "thinking"]
            await s.send("elicit me")
            got = []
            async for ev in s.turn():
                got.append(ev)
                if ev["kind"] == "elicitation":
                    assert ev["server"] == "gmail" and ev["schema"]["properties"]["box"]
                    await s.answer(ev["request_id"], {"action": "accept", "content": {"box": "Inbox"}})
            assert "".join(e["text"] for e in got if e["kind"] == "text") == "Mailbox Inbox."
        finally:
            await s.close()
    run(go())


def test_ekis_tools_are_served_in_process_and_the_program_can_call_them():
    class Eng:
        async def describe(self):
            return [{"key": "qwen", "label": "Qwen", "kind": "mlx", "ok": True, "capabilities": {"text": True}}]
    async def go():
        s = await _session(bridge=mcpbridge.Bridge(Eng(), "c1"))
        try:
            servers = await s.mcp_status()
            eki = next(x for x in servers if x["name"] == "eki")
            assert "eki_capabilities" in [t["name"] for t in eki["tools"]]
            await s.send("use tools")
            text = "".join([ev["text"] async for ev in s.turn() if ev["kind"] == "text"])
            assert "qwen: Qwen [mlx] up (text)" in text
        finally:
            await s.close()
    run(go())


def test_the_bridge_speaks_json_rpc():
    b = mcpbridge.Bridge(None, "")
    async def go():
        init = await b.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05"}})
        assert init["result"]["protocolVersion"] == "2024-11-05" and init["result"]["serverInfo"]["name"] == "eki"
        assert (await b.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))["id"] == 0
        tools = (await b.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))["result"]["tools"]
        assert {"eki_ask", "eki_image", "eki_capabilities"} <= {t["name"] for t in tools}
        bad = await b.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        assert bad["result"]["isError"]
        unknown = await b.handle({"jsonrpc": "2.0", "id": 4, "method": "x/y"})
        assert unknown["error"]["code"] == -32601
        # nesting stops: an agent asking eki asking an agent…
        deep = mcpbridge.Bridge(object(), "", depth=mcpbridge.MAX_DEPTH)
        said = (await deep.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": "eki_ask", "arguments": {"prompt": "hi"}}}))
        assert "refused" in said["result"]["content"][0]["text"]
    run(go())


def test_the_registry_renders_into_both_clis(tmp_path, monkeypatch):
    monkeypatch.setattr(mcpregistry, "PATH", tmp_path / "mcp.json")
    codex = tmp_path / "config.toml"
    codex.write_text('model = "gpt-5"\n[mcp_servers.mine]\ncommand = "mine"\n')
    monkeypatch.setattr(mcpregistry, "CODEX_CONFIG", codex)
    monkeypatch.setattr(mcpregistry, "eki_stdio_command", lambda: ["/py", "-m", "eki.cli", "mcp"])
    mcpregistry.put("fs", {"command": "npx -y @modelcontextprotocol/server-filesystem /tmp"})
    mcpregistry.put("remote", {"url": "https://mcp.example/x", "headers": {"Authorization": "Bearer t"},
                               "backends": ["codex"]})
    with pytest.raises(ValueError):
        mcpregistry.put("eki", {"command": "x"})
    with pytest.raises(ValueError):
        mcpregistry.put("bad name", {"command": "x"})
    doc = mcpregistry.for_claude()
    assert doc["mcpServers"]["fs"] == {"type": "stdio", "command": "npx",
                                       "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
    assert "remote" not in doc["mcpServers"]                 # codex only
    argv = mcpregistry.claude_argv()
    assert argv[0] == "--mcp-config" and json.loads(argv[1]) == doc
    toml = codex.read_text()
    assert toml.startswith('model = "gpt-5"\n[mcp_servers.mine]\ncommand = "mine"\n')   # untouched
    assert '[mcp_servers.eki]\ncommand = "/py"' in toml
    assert '[mcp_servers.fs]' in toml and 'url = "https://mcp.example/x"' in toml
    assert toml.count(mcpregistry.BEGIN) == 1
    mcpregistry.set_enabled("fs", False)
    assert "fs" not in mcpregistry.for_claude()["mcpServers"]
    assert "[mcp_servers.fs]" not in codex.read_text()
    mcpregistry.remove("fs")
    assert "fs" not in mcpregistry.load()
    imported = mcpregistry.import_from_claude(
        [{"name": "gh", "status": "connected", "scope": "user",
          "config": {"type": "stdio", "command": "gh-mcp", "args": []}},
         {"name": "eki", "status": "connected", "config": {"type": "sdk"}}], ["gh", "eki"])
    assert imported["gh"]["origin"] == "claude:user" and "eki" not in imported


def _engine(tmp_path, monkeypatch):
    from eki import settings, secrets
    from eki.adapters.base import BackendInfo, Capabilities, Cost
    from eki.config import Config
    from eki.engine import Engine
    from eki.adapters import claude_code as cc
    from tests.test_providers import fake_keychain  # noqa: F401
    monkeypatch.setattr(secrets, "get", lambda k: None)
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(mcpregistry, "PATH", tmp_path / "mcp.json")
    monkeypatch.setattr(mcpregistry, "CODEX_CONFIG", tmp_path / "config.toml")
    cfg = Config(db_path=str(tmp_path / "eki.db"))
    cfg.backends = [BackendInfo(key="claude", kind="claude_code", label="Claude",
                                capabilities=Capabilities(context_tokens=200000, repo=True, tools=True),
                                cost=Cost(tier=50))]
    cfg.options = {"claude": {"binary": FAKE[0]}}
    real = cc.ClaudeCodeBackend.live_argv

    def argv(self, cwd, resume, sid):
        out = real(self, cwd, resume, sid)
        return [out[0], FAKE[1]] + out[1:]
    monkeypatch.setattr(cc.ClaudeCodeBackend, "live_argv", argv)
    monkeypatch.setattr(cc.ClaudeCodeBackend, "_no_bare_flag", lambda self: None)
    return Engine(cfg)


def test_the_engine_answers_the_panels_for_a_thread_and_rewinds_by_turn(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)

    async def go():
        # before any thread: the folder's warm session answers
        panel = await eng.claude_control("mcp")
        assert {s["name"] for s in panel["servers"]} >= {"docs", "gmail", "eki"}     # eki: the in-process tools
        assert (await eng.claude_control("models"))["account"]["email"] == "x@example.com"
        # a thread with one turn: its checkpoint is kept on the answer
        started = await eng.ask("hello")
        rid, cid = started["run"], started["conversation"]
        q = eng.runner.subscribe(rid)
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        turns = eng.store.turns(cid)
        checkpoint = json.loads(turns[-1]["meta"])["checkpoint"]
        assert len(checkpoint) == 36                       # the uuid eki stamped on the turn
        rewound = await eng.claude_control("rewind", cid, turn=turns[-1]["id"], dry_run=True)
        assert rewound["rewind"] == {"rewound": "known", "dry_run": True}   # the program knows that id
        assert (await eng.claude_control("permission_mode", cid, mode="plan"))["permission_mode"] == "plan"
        assert (await eng.claude_control("effort", cid, effort="low"))["effort"] == "low"
        assert (await eng.claude_control("settings", cid))["settings"]["applied"]["effort"] == "low"   # the flag layer took it
        assert (await eng.claude_control("usage", cid))["usage"]["subscription_type"] == "pro"
        assert (await eng.claude_control("context", cid))["context"]["percentage"] == 1.2
        assert (await eng.claude_control("mcp_authenticate", cid, name="gmail"))["reply"]["opened"]
        assert [s["name"] for s in (await eng.claude_control("skills", cid))["skills"]] == ["docx"]
        names = [c["name"] for c in await eng.commands_for(cid, backend_key="claude")]
        assert "mcp" in names and "memory" in names and "model" not in names and "plugins" not in names
        assert await eng.commands_for(cid) == []                   # Auto: no program's commands
        assert (await eng.agent_control("status", cid))["alive"]     # the thread says which program
        # the registry, applied to the open session without a restart
        mcpregistry.put("fs", {"command": "fs-mcp"})
        assert "servers" in await eng.claude_control("mcp_apply", cid)
        from eki.adapters.base import BackendError
        with pytest.raises(BackendError):
            await eng.claude_control("nothing", cid)
        with pytest.raises(BackendError):
            await eng.claude_control("mcp_toggle", cid)            # needs a name
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_an_elicitation_is_a_pending_card_the_app_answers(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)

    async def go():
        started = await eng.ask("please elicit")
        rid = started["run"]
        q = eng.runner.subscribe(rid)
        seen = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            seen.append(ev["event"])
            if ev["event"] == "elicitation":
                assert ev["server"] == "gmail" and eng.pending[rid]["request_id"] == ev["request_id"]
                assert await eng.answer(rid, ev["request_id"], {"action": "accept", "content": {"box": "Sent"}})
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        assert "elicitation" in seen
        assert eng.store.turns(started["conversation"])[-1]["content"] == "Mailbox Sent."
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_claude_codes_own_computer_use_server_is_declared_on_a_mac(tmp_path, monkeypatch):
    from eki import settings
    monkeypatch.setattr(mcpregistry, "PATH", tmp_path / "mcp.json")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(mcpregistry.sys, "platform", "darwin")
    assert mcpregistry.builtin_for_claude("/usr/local/bin/claude") == {}          # opt-in: off by default
    settings.save({"claude_builtin_computer_use": True})
    assert mcpregistry.builtin_for_claude("/usr/local/bin/claude") == {
        "computer-use": {"type": "stdio", "command": "/usr/local/bin/claude", "args": ["--computer-use-mcp"]}}
    settings.save({"claude_screen": False})
    assert mcpregistry.builtin_for_claude("/usr/local/bin/claude") == {}          # off: nothing declared
    settings.save({"claude_screen": True})
    monkeypatch.setattr(mcpregistry.sys, "platform", "linux")
    assert mcpregistry.builtin_for_claude("/usr/local/bin/claude") == {}          # not a Mac
    with pytest.raises(ValueError):
        mcpregistry.put("computer-use", {"command": "x"})                         # eki's to provide


def test_extra_servers_go_in_right_after_the_handshake():
    async def go():
        s = live.LiveSession(FAKE, None, None,
                             extra_servers={"computer-use": {"type": "stdio", "command": "claude",
                                                             "args": ["--computer-use-mcp"]}})
        await s.start()
        try:
            servers = await s.mcp_status()
            assert "computer-use" in {x["name"] for x in servers}
        finally:
            await s.close()
    run(go())


def test_reconnect_on_a_disabled_server_enables_it_first(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)

    async def go():
        await eng.claude_control("mcp_toggle", name="docs", enabled=False)
        assert "servers" in await eng.claude_control("mcp_reconnect", name="docs")
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_a_picture_attached_to_the_question_reaches_the_program(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    pic = tmp_path / "shot.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)

    async def go():
        started = await eng.ask("what is this", attachments=[str(pic), str(tmp_path / "missing.png")])
        rid, cid = started["run"], started["conversation"]
        q = eng.runner.subscribe(rid)
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["event"] == "state" and ev["state"] in ("done", "failed"):
                break
        turns = eng.store.turns(cid)
        assert turns[0]["content"].startswith("what is this\n\n![attachment](")   # shown with the question
        assert json.loads(turns[0]["meta"])["attachments"] == [str(pic)]          # the missing one dropped
        assert "[+1 image, image/png]" in turns[-1]["content"]                     # the program got it
        await eng.quota.stop()
        await eng.close()
    run(go())


def test_the_screen_tools_being_kept_out_by_macos_is_a_card_not_a_line():
    assert live.permission_needed("The user saw the permission prompt but macOS Accessibility "
                                  "permission(s) are still not granted.") == "accessibility"
    assert live.permission_needed("Screen Recording permission is required to capture") == "screen"
    assert live.permission_needed("read 12 lines") == ""
