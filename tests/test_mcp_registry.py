import json

from eki import capacity, cli, mcp, paths, providers


def _names(file):
    p = paths.config(file)
    return set(json.loads(p.read_text())["mcpServers"]) if p.exists() else set()


def test_add_off_on_remove():
    mcp.add("fs", command="npx", args=["-y", "srv"], env={"K": "secret-value-xy"})
    assert _names("mcp") == {"fs"}
    assert mcp.claude_config() and any("mcp_servers.fs" in f for f in mcp.codex_config())

    mcp.set_on("fs", False)
    assert _names("mcp") == set() and _names("mcp-off") == {"fs"}
    assert mcp.claude_config() is None and mcp.codex_config() == []
    assert mcp.all_servers() == [{"name": "fs", "spec": {"command": "npx", "args": ["-y", "srv"],
                                                          "env": {"K": "secret-value-xy"}}, "on": False}]
    mcp.set_on("fs", True)
    assert _names("mcp") == {"fs"} and _names("mcp-off") == set()
    assert [r["on"] for r in mcp.all_servers()] == [True]

    mcp.set_on("fs", False)
    assert mcp.remove("fs") is True
    assert mcp.all_servers() == [] and mcp.remove("fs") is False


def test_set_on_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        mcp.set_on("nope", True)


def test_public_masks():
    spec = {"command": "x", "args": [], "env": {"TOKEN": "ghp_supersecret42"}}
    out = mcp.public("x", spec)
    assert out["env"]["TOKEN"] == "••••42"
    assert "ghp_supersecret42" not in json.dumps(out)
    assert spec["env"]["TOKEN"] == "ghp_supersecret42"     # the original is untouched


def test_blank_env_on_readd_keeps_value():
    mcp.add("s", command="a", env={"K": "keep-me", "J": "j-old"})
    mcp.add("s", command="b", env={"K": "", "J": "j-new"})
    assert mcp.servers()["s"] == {"command": "b", "args": [], "env": {"K": "keep-me", "J": "j-new"}}
    mcp.set_on("s", False)
    mcp.add("s", command="c", env={"K": ""})               # editing an off server keeps it off
    assert mcp.off()["s"]["env"] == {"K": "keep-me"} and "s" not in mcp.servers()


def test_programs_follow_capacity(monkeypatch):
    monkeypatch.setattr(providers, "config", lambda: {"claude": {"kind": "claude_code"},
                                                      "codex": {"kind": "codex"}})
    monkeypatch.setattr(capacity, "status", lambda conn, background=False:
                        {"claude": (True, "ok"), "codex": (False, "not installed")})
    mcp.add("a", command="x")
    mcp.add("b", url="https://b/mcp")
    mcp.set_on("b", False)
    assert mcp.programs() == {"a": {"claude": True, "codex": False}, "b": {"claude": False, "codex": False}}


def test_cli_list_masks_and_switches(capsys):
    assert cli.main(["mcp", "add", "web", "--env", "KEY=abcdef123", "--", "npx", "srv"]) == 0
    assert cli.main(["mcp", "off", "web"]) == 0
    capsys.readouterr()
    assert cli.main(["mcp", "list"]) == 0
    out = capsys.readouterr().out
    assert "web" in out and "off" in out and "KEY=••••23" in out and "abcdef123" not in out
    assert cli.main(["mcp", "on", "web"]) == 0
    assert "web" in mcp.servers()
    assert cli.main(["mcp", "off", "ghost"]) == 1
