# SPDX-License-Identifier: Apache-2.0
"""A run an agent starts through eki gets what its parent hands it, never
more — decided in eki, rendered into each program's own settings."""
import json
import os

import pytest

from eki import grant, settings
from eki.adapters.base import Backend, BackendError, BackendInfo, Capabilities, Cost, Health
from tests.test_runs import echo_config, settle

FULL = grant.FULL


# ---- deciding ------------------------------------------------------------------------------

def test_what_an_agents_request_asks_for():
    assert grant.asked().level == "read"                                    # a question, a review
    assert grant.asked(repo=True, commands=["pytest"]) == grant.Grant("write", (), ("pytest",))
    assert grant.asked(repo=True, read_only=True).level == "read"
    image = grant.asked(images=True)
    assert image.level == "write" and image.paths == (os.path.realpath(grant.IMAGES),)
    assert image.commands == ()


def test_the_top_of_the_tree_hands_on_what_is_asked():
    assert grant.for_agent(FULL, repo=True, commands=["pytest", "git diff"]).commands == ("pytest", "git diff")


def test_a_child_never_gets_more_than_its_parent():
    parent = grant.Grant("write", ("/w",), ("git", "pytest"))
    child = grant.narrow(parent, grant.Grant("write", ("/w/sub", "/etc"), ("git log", "rm", "pytest -q")))
    assert child.paths == ("/w/sub",)                                       # /etc isn't the parent's
    assert child.commands == ("git log", "pytest -q")                        # rm isn't
    reader = grant.Grant("read", (), ())
    assert grant.narrow(reader, grant.asked(repo=True, commands=["pytest"])) == grant.Grant("read", (), ())
    # "git" covers "git log"; "git log" doesn't cover "git"; "gitk" isn't "git"
    assert grant.narrow(grant.Grant("read", (), ("git log",)), grant.Grant("read", (), ("git", "gitk"))).commands == ()


def test_a_grant_crosses_into_a_childs_shell(monkeypatch):
    g = grant.Grant("write", ("/w",), ("pytest",))
    env = grant.env(g)
    assert grant.load(env[grant.ENV]) == g
    monkeypatch.setenv(grant.ENV, env[grant.ENV])
    assert grant.from_env() == g
    monkeypatch.delenv(grant.ENV)
    assert grant.from_env() == FULL and grant.ENV not in grant.env(FULL)


def test_what_cant_be_read_is_the_least_not_the_most():
    assert grant.load("{not json") == grant.Grant("read", (), ())
    assert grant.load({"level": "root"}).level == "read"
    assert grant.load({"level": "write"}).commands == ()                    # no list: no commands


# ---- rendering -----------------------------------------------------------------------------

def test_claude_code_gets_a_mode_and_tool_lists():
    assert grant.claude_argv(FULL) == []
    argv = grant.claude_argv(grant.Grant("read", (), ()))
    assert argv[argv.index("--permission-mode") + 1] == "default"
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Edit" in denied and "Write" in denied and "Bash" in denied
    argv = grant.claude_argv(grant.Grant("write", ("/imgs",), ("pytest",)))
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash(pytest:*)" in argv and "Edit" in argv and "Bash" not in argv
    assert argv[-2:] == ["--add-dir", "/imgs"]


def test_codex_gets_its_sandbox():
    assert grant.codex_argv(FULL) == []
    assert grant.codex_argv(grant.Grant("read", (), ())) == ["-s", "read-only"]
    argv = grant.codex_argv(grant.Grant("write", ("/imgs",), ()))
    assert argv[:2] == ["-s", "workspace-write"]
    assert json.loads(argv[3].split("=", 1)[1]) == ["/imgs"]


def test_the_adapters_render_it_over_the_permissions_setting(tmp_path, monkeypatch):
    from eki.adapters.claude_code import ClaudeCodeBackend
    from eki.adapters.gemini_cli import GeminiCliBackend
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ClaudeCodeBackend, "_no_bare_flag", lambda self: None)
    b = ClaudeCodeBackend(BackendInfo(key="claude", kind="claude_code", label="c"), {"binary": "/bin/echo"})
    assert "--dangerously-skip-permissions" in b._argv("hi", None, "/tmp/p")          # yours: "auto"
    narrowed = b._argv("hi", None, "/tmp/p", grant.Grant("read", (), ()))
    assert "--dangerously-skip-permissions" not in narrowed
    assert narrowed.count("--permission-mode") == 1 and "--disallowedTools" in narrowed
    g = GeminiCliBackend(BackendInfo(key="gemini", kind="gemini_cli", label="g"), {"binary": "/bin/echo"})
    argv = g._argv("hi", None, None, grant.Grant("read", (), ()))
    assert argv[argv.index("--approval-mode") + 1] == "default"


# ---- what a narrowed run was refused --------------------------------------------------------

def test_a_refusal_says_what_it_wanted_and_what_would_allow_it():
    r = grant.refusal("Bash", {"command": "git push origin main"})
    assert r["command"] == "git push" and "git push origin main" in r["what"]
    assert grant.refusal("Bash", {"command": "FOO=1 pytest -q"})["command"] == "pytest"
    e = grant.refusal("Write", {"file_path": "/tmp/x/notes.md"})
    assert e["edit"] and e["path"] == os.path.realpath("/tmp/x")
    m = grant.refusal("mcp__github__create_issue", {})
    assert "command" not in m and "edit" not in m and m["what"].startswith("use ")


def test_allowing_adds_what_was_wanted_and_stays_narrowed():
    g = grant.Grant("read", (), ("git log",))
    wider = grant.widened(g, [grant.refusal("Bash", {"command": "git log -3"}),
                              grant.refusal("Bash", {"command": "pytest -q"})])
    assert wider == grant.Grant("read", (), ("git log", "pytest"))       # git log already covered it
    edits = grant.widened(g, [grant.refusal("Edit", {"file_path": "/tmp/x/a.py"})])
    assert edits.level == "write" and edits.paths == (os.path.realpath("/tmp/x"),)
    assert grant.widened(grant.FULL, [grant.refusal("Bash", {"command": "ls"})]) == grant.FULL
    assert "eki allow abc" in grant.refused_line([grant.refusal("Bash", {"command": "ls"})], "abc")


def test_claude_codes_denials_are_read_back(tmp_path, monkeypatch):
    import asyncio
    from eki.adapters.claude_code import ClaudeCodeBackend
    from eki.adapters.base import Message
    monkeypatch.setattr(settings, "load", lambda: {"permissions": "ask"})
    fake = tmp_path / "claude"
    denial = {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "npm install left-pad"}}
    fake.write_text("#!/bin/sh\n" + "echo '" + json.dumps(
        {"type": "result", "is_error": False, "result": "no", "permission_denials": [denial]}) + "'\n")
    fake.chmod(0o755)
    b = ClaudeCodeBackend(BackendInfo(key="c", kind="claude_code", label="C"), {"binary": str(fake)})
    monkeypatch.setattr(b, "_no_bare_flag", lambda: None)

    async def drain():
        return [c async for c in b.stream([Message("user", "hi")], grant=grant.Grant("read", (), ()))]
    asyncio.run(drain())
    assert b.last_refused == [grant.refusal("Bash", {"command": "npm install left-pad"})]


# ---- in a run ------------------------------------------------------------------------------

class Harness(Backend):
    kw = []
    fail = False
    refused = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Harness.kw.append(kw)
        self.last_refused = list(Harness.refused)
        if Harness.fail:
            raise BackendError("SyntaxError in app.py")
        yield "done"


def desk(tmp_path, monkeypatch):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends = [BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                capabilities=Capabilities(tools=True, repo=True, web=True),
                                cost=Cost(tier=50))]
    cfg.options = {"claude_code": {"binary": "/bin/echo"}}
    monkeypatch.setattr(adapters, "build", lambda info, opts: Harness(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "skills_local": False,
                  "worktrees": False}
    e.router.is_up = lambda k: True
    # kept-open sessions are for the person's runs; a narrowed one must not reach them
    monkeypatch.setattr(e, "_lives", lambda b: True)

    async def live_turn(*a, **k):
        yield "live"
    monkeypatch.setattr(e, "_live_turn", live_turn)
    Harness.kw, Harness.fail, Harness.refused = [], False, []
    return e


@pytest.mark.asyncio
async def test_an_agents_run_is_handed_its_grant_and_runs_headless(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    s = await eng.ask("review this diff", backend_key="claude_code", via="agent")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "done"
    assert Harness.kw[-1]["grant"] == grant.Grant("read", (), ())
    turn = eng.store.turns(s["conversation"])[-1]
    assert turn["content"] == "done" and json.loads(turn["meta"])["grant"]["level"] == "read"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_retrying_doesnt_widen_it(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    Harness.fail = True
    s = await eng.ask("review this diff", backend_key="claude_code", via="agent")
    assert (await settle(eng.runs, s["run"], timeout=5))["state"] == "failed"
    Harness.fail = False
    again = await eng.retry(s["run"])
    run = await settle(eng.runs, again["run"], timeout=5)
    assert json.loads(run["payload"])["grant"]["level"] == "read"
    assert Harness.kw[-1]["grant"].level == "read"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_grandchild_is_bounded_by_its_parent(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    parent = grant.Grant("read", (), ("git log",)).to_json()
    s = await eng.ask("fix it", backend_key="claude_code", repo=str(tmp_path), parent=parent,
                      wants={"commands": ["git log -3", "rm -rf"]})
    run = await settle(eng.runs, s["run"], timeout=5)
    g = grant.load(json.loads(run["payload"])["grant"])
    assert g == grant.Grant("read", (), ("git log -3",))                    # via set by the parent alone
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_your_own_run_is_untouched(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    s = await eng.ask("hello", backend_key="claude_code")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert "grant" not in (run.get("payload") or "")
    assert eng.store.turns(s["conversation"])[-1]["content"] == "live"      # the kept-open session
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_refused_run_says_what_it_wanted_and_can_be_allowed_once(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    Harness.refused = [grant.refusal("Bash", {"command": "pytest -q"})]
    s = await eng.ask("check the tests", backend_key="claude_code", via="agent")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "done"                                         # it carried on
    turn = eng.store.turns(s["conversation"])[-1]
    assert "run `pytest -q`" in turn["content"] and f"eki allow {s['run']}" in turn["content"]
    assert json.loads(turn["meta"])["allow"]["commands"] == ["pytest"]
    Harness.refused = []
    again = await eng.allow(s["run"])
    rerun = await settle(eng.runs, again["run"], timeout=5)
    assert Harness.kw[-1]["grant"] == grant.Grant("read", (), ("pytest",))
    assert json.loads(rerun["payload"])["retry_of"] == s["run"]
    assert await eng.allow(s["run"]) is None                              # once
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_refused_run_that_failed_says_so_too(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    Harness.fail = True
    Harness.refused = [grant.refusal("Write", {"file_path": str(tmp_path / "out.md")})]
    s = await eng.ask("write the notes", backend_key="claude_code", via="agent")
    assert (await settle(eng.runs, s["run"], timeout=5))["state"] == "failed"
    turn = eng.store.turns(s["conversation"])[-1]
    assert "wasn't allowed to edit" in turn["content"] and json.loads(turn["meta"])["failed"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_an_agent_allowing_is_bounded_by_what_it_has(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    Harness.refused = [grant.refusal("Bash", {"command": "git push"}),
                       grant.refusal("Bash", {"command": "pytest"})]
    s = await eng.ask("ship it", backend_key="claude_code", via="agent")
    await settle(eng.runs, s["run"], timeout=5)
    Harness.refused = []
    again = await eng.allow(s["run"], grant.Grant("read", (), ("pytest",)).to_json())
    await settle(eng.runs, again["run"], timeout=5)
    assert Harness.kw[-1]["grant"] == grant.Grant("read", (), ("pytest",))
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_nothing_refused_nothing_to_allow(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    s = await eng.ask("review this diff", backend_key="claude_code", via="agent")
    await settle(eng.runs, s["run"], timeout=5)
    assert await eng.allow(s["run"]) is None
    assert "allow" not in eng.store.turns(s["conversation"])[-1]["content"]
    await eng.runner.stop()
