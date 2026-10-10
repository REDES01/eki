"""A project's mcp cards reach that project's Claude and Codex runs, for the run only, secrets masked."""
import json
import stat
from pathlib import Path

from eki import apiroutes, mcp, paths, store, worker
from eki.components import mcpserver
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex

SECRET = "ghp_supersecret42"


def _card(root, name, server, about="a project tool"):
    d = root / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "mcp", "about": about, "server": server}))
    return d


def _project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, pid, str(folder), "trunk", 0, str(folder)))
    conn.commit()


def _turn(conn, cwd):
    tid = store.create_thread(conn, "t", str(cwd))
    rid = store.create_run(conn, tid, "hi")
    return worker.build_turn(conn, store.run(conn, rid), "fake")


def _claude_servers(turn):
    argv = ClaudeCode("claude", {"kind": "claude_code"}).argv(turn)
    files = [argv[i + 1] for i, a in enumerate(argv) if a == "--mcp-config"]
    assert len(files) == 1
    return files[0], json.loads(Path(files[0]).read_text())["mcpServers"]


def _call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def test_card_reaches_claude_and_codex_and_globals_untouched(conn, proj, tmp_path):
    mcp.add("shared", command="reg-tool", args=["-x"])
    mcp.add("sleepy", command="off-tool")
    mcp.set_on("sleepy", False)
    _card(proj, "game-db", {"command": "dbtool", "args": ["--ro"], "env": {"TOKEN": SECRET}})
    globals_ = [paths.config("mcp"), paths.config("mcp-off")]
    before = [f.read_bytes() for f in globals_]
    t = _turn(conn, proj)
    f, got = _claude_servers(t)
    assert Path(f) == paths.home() / "mcp-runs" / f"{t.run_id}.json"
    assert stat.S_IMODE(Path(f).stat().st_mode) == 0o600
    assert got == {"shared": {"command": "reg-tool", "args": ["-x"]},
                   "game-db": {"command": "dbtool", "args": ["--ro"], "env": {"TOKEN": SECRET}}}
    argv = Codex("codex", {}).argv(t)
    assert 'mcp_servers.game-db.command="dbtool"' in argv and 'mcp_servers.shared.command="reg-tool"' in argv
    assert not any("sleepy" in a for a in argv)
    assert [f.read_bytes() for f in globals_] == before
    user = tmp_path / "user"
    assert not any((user / n).exists() for n in (".claude", ".claude.json", ".codex"))


def test_other_project_or_no_project_gets_the_registry(conn, proj, tmp_path):
    mcp.add("shared", command="reg-tool")
    _card(proj, "game-db", {"url": "https://db.example/mcp"})
    other = tmp_path / "b"
    (other / "components").mkdir(parents=True)
    loose = tmp_path / "loose"
    loose.mkdir()
    for cwd in (other, loose):
        t = _turn(conn, cwd)
        assert (t.extra["claude_mcp"], t.extra["codex_config"]) == (mcp.claude_config(), mcp.codex_config())
    assert mcpserver.run_config(None, "r1") == (mcp.claude_config(), mcp.codex_config())
    assert mcpserver.servers_of(proj) == {"game-db": {"type": "http", "url": "https://db.example/mcp"}}
    assert not (paths.home() / "mcp-runs").exists()


def test_many_cards_and_a_card_wins_a_clash(conn, proj):
    mcp.add("search", command="registry-search")
    _card(proj, "search", {"command": "project-search"})
    _card(proj, "docs", {"url": "https://docs.example/mcp"})
    (proj / "components" / "broken").mkdir()
    (proj / "components" / "broken" / "component.json").write_text('{"kind": "mcp", "about": "x"}')
    _, got = _claude_servers(_turn(conn, proj))
    assert got == {"search": {"command": "project-search", "args": []},
                   "docs": {"type": "http", "url": "https://docs.example/mcp"}}


def test_card_api_masks_secrets_and_saving_masked_keeps_them(conn, proj):
    _project(conn, proj)
    card = _card(proj, "game-db", {"command": "dbtool", "env": {"TOKEN": SECRET, "MODE": "ro-mode"}})
    listed = _call(conn, "GET", "/api/projects/p1/cards")
    assert SECRET not in json.dumps(listed)
    shown = {c["name"]: c for c in listed["cards"]}["game-db"]["spec"]
    assert shown["server"]["env"]["TOKEN"] == "••••42"
    server = dict(shown["server"], env=dict(shown["server"]["env"], MODE="rw-mode"), args=["--v"])
    got = _call(conn, "POST", "/api/projects/p1/cards/game-db", {"settings": {"server": server}})
    assert SECRET not in json.dumps(got)
    stored = json.loads((card / "component.json").read_text())["server"]
    assert stored == {"command": "dbtool", "env": {"TOKEN": SECRET, "MODE": "rw-mode"}, "args": ["--v"]}
    server["env"]["TOKEN"] = "new-token-99"
    _call(conn, "POST", "/api/projects/p1/cards/game-db", {"settings": {"server": server}})
    assert json.loads((card / "component.json").read_text())["server"]["env"]["TOKEN"] == "new-token-99"
