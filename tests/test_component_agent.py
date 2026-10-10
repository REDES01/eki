"""The agent kind: a persona called as a session; bare means its own prompt and nothing else."""
import json
import shutil
from pathlib import Path

from eki import components, mcp
from eki.components import Call, agent
from eki.providers.base import Outcome, Turn
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


def test_bare_on_default_claude_is_valid_without_providers_entry(proj, home):
    d = proj / "components" / "dflt"
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("x")
    assert agent.validate({"kind": "agent", "target": "claude:opus@high", "bare": True}, d) == []
    assert agent.validate({"kind": "agent", "target": "local", "bare": True}, d)


def playtester(proj, home):
    with_claude(home)
    src = components.templates_dir() / "playtester"
    shutil.copytree(src, proj / "components" / "playtester")
    mcp.save(json.loads((src / "component.json").read_text())["mcp_servers"])
    return components.get("playtester", proj)


def test_allow_list_is_all_that_reaches_claude(proj, home):
    comp = playtester(proj, home)
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(comp, call_of(proj, extra={"allowed_tools": ["Bash"]}), s)
    argv = argv_of(turn)
    assert argv[argv.index("--allowedTools") + 1].split(",") == comp.spec["allow_tools"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "bypassPermissions" not in argv and "--allow-dangerously-skip-permissions" not in argv
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
    assert "mcp__playwright__browser_evaluate" in argv[argv.index("--disallowedTools") + 1].split(",")
    args = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())["mcpServers"]["playwright"]["args"]
    assert ("--snapshot-mode", "none") in list(zip(args, args[1:]))


def test_without_allow_list_permissions_are_as_before(proj, home):
    with_claude(home)
    comp = put(proj, "plain", {"bare": True})
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(comp, call_of(proj), s)
    argv = argv_of(turn)
    assert "--allowedTools" not in argv and argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_allow_tools_is_checked(proj):
    d = components.templates_dir() / "playtester"
    for bad in ({"target": "claude", "bare": True, "mcp": ["pw"], "allow_tools": ["mcp__other__a"]},
                {"target": "claude", "bare": True, "mcp": ["pw"], "allow_tools": "mcp__pw__a"},
                {"target": "claude", "mcp": ["pw"], "allow_tools": ["mcp__pw__a"]}):
        assert any("allow_tools" in e for e in agent.validate(bad, d))
    assert agent.tool_rules({"mcp": ["pw"], "allow_tools": ["mcp__pw__a"]}) == {}   # not bare: no allow-list
    assert agent.tool_rules({"bare": True, "mcp": ["pw"], "allow_tools": ["mcp__pw__a"]}) == \
        {"allowed_tools": ["mcp__pw__a"]}


class Player:
    def __init__(self, urls):
        self.urls = urls

    def take(self, turn, emit):
        for u in self.urls:
            emit("tool", {"name": "mcp__playwright__browser_navigate", "detail": "…", "url": u})
        emit("text", {"text": "REVIEW: fine"})
        return Outcome()


def test_a_call_reads_its_navigates(proj, home, monkeypatch):
    from eki import providers
    comp = playtester(proj, home)
    url = comp.spec["url"]
    monkeypatch.setattr(providers, "get", lambda name: Player([url, url + "next.html"]))
    r = agent.call(comp, call_of(proj))
    assert not r.error and not r.text.startswith("tainted")
    away = url.split("/chapter1/")[0] + "/src/game.js"
    monkeypatch.setattr(providers, "get", lambda name: Player([url, away]))
    r = agent.call(comp, call_of(proj))
    assert r.text.startswith(f"tainted: opened {away}")
    assert json.loads((Path(r.paths[0]) / "session.json").read_text())["tainted"] == f"opened {away}"
