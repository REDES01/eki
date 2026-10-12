"""Every run's Playwright looks up its browsers in the browser shim: the run's env, and each
Playwright MCP server in the run's config, carry PLAYWRIGHT_BROWSERS_PATH; the registry stays as it was."""
import json
from pathlib import Path

from eki import browsershim, mcp, paths, store, worker
from eki.components import mcpserver
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex


def _turn(conn, cwd, provider="fake", row=None):
    tid = store.create_thread(conn, "t", str(cwd))
    rid = store.create_run(conn, tid, "hi", provider=provider, row=row)
    return worker.build_turn(conn, store.run(conn, rid), provider)


def test_a_chat_and_a_command_run_get_the_shim(conn, tmp_path):
    for provider, row in (("fake", None), ("command", None), ("fake", "chore")):
        env = _turn(conn, tmp_path, provider, row).extra["env"]
        assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(browsershim.mirror())
    assert browsershim.mirror().is_dir()


def test_a_broken_shim_never_fails_the_run(conn, tmp_path, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(browsershim, "ensure", boom)
    t = _turn(conn, tmp_path)
    assert "PLAYWRIGHT_BROWSERS_PATH" not in (t.extra.get("env") or {})


def test_a_playwright_server_carries_the_variable_and_mcp_json_is_unchanged(conn, tmp_path):
    mcp.add("playwright", command="npx", args=["@playwright/mcp@latest", "--headless"], env={"K": "v1"})
    mcp.add("other", command="reg-tool")
    before = paths.config("mcp").read_bytes()
    t = _turn(conn, tmp_path)
    f = t.extra["claude_mcp"]
    assert Path(f) == paths.home() / "mcp-runs" / f"{t.run_id}.json"
    got = json.loads(Path(f).read_text())["mcpServers"]
    assert got["playwright"]["env"] == {"K": "v1", "PLAYWRIGHT_BROWSERS_PATH": str(browsershim.mirror())}
    assert "env" not in got["other"]
    argv = ClaudeCode("claude", {"kind": "claude_code"}).argv(t)
    assert argv[argv.index("--mcp-config") + 1] == f
    codex = Codex("codex", {}).argv(t)
    assert any(a.startswith("mcp_servers.playwright.env=") and str(browsershim.mirror()) in a for a in codex)
    assert paths.config("mcp").read_bytes() == before


def test_a_playwright_card_carries_the_variable(conn, proj):
    d = proj / "components" / "playtester"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps(
        {"kind": "mcp", "about": "plays", "server": {"command": "playwright-mcp"}}))
    _, flags = mcpserver.run_config(proj, "r1", conn)
    got = json.loads((paths.home() / "mcp-runs" / "r1.json").read_text())["mcpServers"]
    assert got["playtester"]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browsershim.mirror())
    assert any("PLAYWRIGHT_BROWSERS_PATH" in a for a in flags)


def test_no_playwright_server_keeps_the_registry_file(conn, tmp_path):
    mcp.add("other", command="reg-tool")
    t = _turn(conn, tmp_path)
    assert t.extra["claude_mcp"] == mcp.claude_config()
    assert not (paths.home() / "mcp-runs").exists()
