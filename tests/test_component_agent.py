"""The agent kind: a persona called as a session; bare means its own prompt and nothing else."""
import json
from pathlib import Path

from eki import components, mcp
from eki.components import Call, agent
from eki.providers.base import Turn
from eki.providers.claude_code import ClaudeCode


def put(proj, name, spec, prompt="You play games. Open {url}.", skills=False):
    d = proj / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "agent", "about": "a persona", **spec}))
    (d / "prompt.md").write_text(prompt)
    if skills:
        (d / "skills" / "notes").mkdir(parents=True)
        (d / "skills" / "notes" / "SKILL.md").write_text("---\nname: notes\n---\n")
    return components.get(name, proj)


def with_claude(home):
    f = home / "providers.json"
    cfg = json.loads(f.read_text())
    cfg["claude"] = {"kind": "claude_code", "binary": "claude"}
    f.write_text(json.dumps(cfg))


def call_of(proj, *args, extra=None):
    return Call(args=list(args), caller_dir=str(proj), caller_run="r0", run_id="r1",
                emit=lambda k, d: None, extra=dict(extra or {}))


def argv_of(turn):
    return ClaudeCode("claude", {"kind": "claude_code"}).argv(turn)


def test_bare_argv_carries_its_prompt_and_nothing_else(proj, home):
    with_claude(home)
    mcp.save({"playwright": {"command": "npx", "args": ["@playwright/mcp", "--output-dir", "{session}/shots"]},
              "other": {"command": "other"}})
    comp = put(proj, "tester", {"target": "claude:opus@high", "bare": True, "mcp": ["playwright"],
                                "deny_tools": ["browser_evaluate"], "url": "http://localhost:5173"})
    session = Path(home / "components" / "sessions" / "tester" / "s1")
    session.mkdir(parents=True)
    hooks = {"claude_plugin": "/eki/plugin", "claude_mcp": "/eki/mcp.json", "note": "eki's note",
             "on_child": print}
    name, turn = agent.turn_for(comp, call_of(proj, "play", "level", "one", extra=hooks), session)
    assert name == "claude" and turn.prompt == "play level one"
    assert turn.extra["on_child"] is print                      # worker hooks pass through
    argv = argv_of(turn)
    sp = argv[argv.index("--system-prompt") + 1]
    assert sp == "You play games. Open http://localhost:5173."
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    assert argv[argv.index("--disallowedTools") + 1] == "browser_evaluate"
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "high"
    for gone in ("--plugin-dir", "--add-dir", "--append-system-prompt"):
        assert gone not in argv
    assert argv.count("--mcp-config") == 1
    written = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())["mcpServers"]
    assert list(written) == ["playwright"]
    assert written["playwright"]["args"][-1] == f"{session}/shots"
    scratch = Path(turn.cwd)
    assert scratch == session / "scratch" and scratch.is_dir() and not any(scratch.iterdir())
    assert not str(scratch.resolve()).startswith(str(proj.resolve()))


def test_bare_without_servers_has_no_mcp_config(proj, home):
    with_claude(home)
    comp = put(proj, "plain", {"bare": True})
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(comp, call_of(proj), s)
    argv = argv_of(turn)
    assert turn.prompt == "Begin."
    assert "--mcp-config" not in argv and "--strict-mcp-config" in argv
    assert "--disallowedTools" not in argv


def test_non_bare_argv_is_unchanged():
    t = Turn(prompt="hi", history=[], cwd="/p", extra={"claude_plugin": "/pl", "claude_mcp": "/m.json",
                                                       "note": "n", "model": "opus"})
    assert argv_of(t)[-10:] == ["--model", "opus", "--add-dir", "/p", "--plugin-dir", "/pl",
                                "--mcp-config", "/m.json", "--append-system-prompt", "n"]
    assert "--system-prompt" not in argv_of(t) and "--setting-sources" not in argv_of(t)


def test_non_bare_agent_gets_note_skills_and_callers_folder(proj, home):
    with_claude(home)
    comp = put(proj, "helper", {"target": "claude"}, prompt="Be kind.", skills=True)
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(comp, call_of(proj, "help"), s)
    assert turn.cwd == str(proj) and turn.extra["note"] == "Be kind."
    argv = argv_of(turn)
    plugin = Path(argv[argv.index("--plugin-dir") + 1])
    assert (plugin / "skills" / "notes" / "SKILL.md").is_file()
    assert "--system-prompt" not in argv and "--add-dir" in argv


def test_two_calls_make_two_sessions(proj, home):
    comp = put(proj, "echo", {"target": "fake"})
    a = agent.call(comp, call_of(proj, "steps=1"))
    b = agent.call(comp, call_of(proj, "steps=1"))
    assert not a.error and not b.error and a.text
    assert a.paths != b.paths
    for r in (a, b):
        assert (Path(r.paths[0]) / "transcript.md").is_file()
        assert Path(r.paths[0]).parent == home / "components" / "sessions" / "echo"


def test_bare_on_another_provider_fails_validation(proj, home):
    with_claude(home)
    d = proj / "components" / "wrong"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "agent", "about": "x", "target": "fake",
                                                  "bare": True}))
    (d / "prompt.md").write_text("x")
    found, errors = components.find(proj)
    assert "wrong" not in found
    assert "Claude Code only" in dict((Path(w).name, why) for w, why in errors)["wrong"]
    assert agent.validate({"kind": "agent", "target": "claude", "bare": True}, d) == []


def test_template_is_a_valid_agent(home):
    folder = components.templates_dir() / "agent"
    comp = components.load(folder, "pool")
    assert comp.kind == "agent" and comp.spec["bare"] is False


def test_unknown_mcp_server_is_an_error(proj, home):
    with_claude(home)
    comp = put(proj, "lost", {"bare": True, "mcp": ["nowhere"]})
    r = agent.call(comp, call_of(proj))
    assert "nowhere" in r.error
