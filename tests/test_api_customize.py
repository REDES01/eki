"""The Customize page's API: skills and connectors through server.serve(), env never whole."""
import base64
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from eki import capacity, github, mcp, paths, providers, server, skills

SECRET = "sk-very-secret-value-42"


@pytest.fixture
def srv(home, monkeypatch):
    monkeypatch.setattr(providers, "config", lambda: {"claude": {"kind": "claude_code"},
                                                      "codex": {"kind": "codex"}})
    monkeypatch.setattr(capacity, "status", lambda conn, background=False:
                        {"claude": (True, "ok"), "codex": (False, "not installed")})
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


def call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Eki": "1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    assert SECRET not in raw and SECRET[-6:] not in raw       # the full value never leaves
    return status, json.loads(raw)


def _skill(root, name, desc="does a thing"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return d


def _git(where, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(where), *args], check=True,
                   capture_output=True)


@pytest.fixture
def bare(tmp_path):
    src = tmp_path / "src"
    _skill(src / "skills", "tidy", "keeps things tidy")
    _git(src.parent, "init", "-q", str(src))
    _git(src, "add", ".")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "skills")
    repo = tmp_path / "skills.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(repo)], check=True)
    return repo


def test_skills_from_a_folder_and_a_git_url(srv, tmp_path, bare):
    folder = _skill(tmp_path / "mine", "notes", "takes notes")
    assert call(srv + "/api/skills", {"source": str(folder)}) == (200, {"name": "notes"})
    url = f"file://{bare}#skills/tidy"
    assert call(srv + "/api/skills", {"source": url}) == (200, {"name": "tidy"})
    st, got = call(srv + "/api/skills")
    assert st == 200
    assert [(s["name"], s["description"], s["source"]) for s in got["skills"]] == [
        ("notes", "takes notes", str(folder.resolve())), ("tidy", "keeps things tidy", url)]
    assert all(s["added_at"] > 0 and s["path"] for s in got["skills"])
    assert (skills.agents_dir() / "tidy").is_symlink()
    st, md = call(srv + "/api/skills/notes")
    assert st == 200 and md["name"] == "notes" and "description: takes notes" in md["text"]
    assert call(srv + "/api/skills/ghost")[0] == 404
    assert call(srv + "/api/skills/tidy/remove", {})[0] == 200
    assert not os.path.lexists(skills.agents_dir() / "tidy")
    assert "tidy" not in skills.sources()
    assert [s["name"] for s in call(srv + "/api/skills")[1]["skills"]] == ["notes"]
    assert call(srv + "/api/skills/tidy/remove", {})[0] == 404
    st, err = call(srv + "/api/skills", {"source": ""})
    assert st == 400 and err["error"]


def test_skill_discover_marks_installed(srv, tmp_path, monkeypatch):
    def gh(args, cwd=None):
        path = args[1]
        if path == "repos/anthropics/skills/contents/skills":
            return json.dumps([{"name": "notes", "path": "skills/notes", "type": "dir"},
                               {"name": "pdf", "path": "skills/pdf", "type": "dir"}])
        name = path.split("/")[-2]
        md = f"---\nname: {name}\ndescription: Work with {name}\n---\n"
        return json.dumps({"name": "SKILL.md", "content": base64.b64encode(md.encode()).decode()})
    monkeypatch.setattr(github, "_gh", gh)
    skills.add(str(_skill(tmp_path / "mine", "notes")))
    st, got = call(srv + "/api/skills/discover")
    assert st == 200 and got["why"] is None
    assert [(s["name"], s["description"], s["repo"], s["path"], s["installed"]) for s in got["skills"]] == [
        ("notes", "Work with notes", "anthropics/skills", "skills/notes", True),
        ("pdf", "Work with pdf", "anthropics/skills", "skills/pdf", False)]


def test_skill_discover_without_gh_says_why(srv):
    st, got = call(srv + "/api/skills/discover")
    assert st == 200 and got["skills"] == [] and got["why"]


def test_connectors_add_mask_switch_remove(srv):
    body = {"name": "web", "command": "npx", "args": ["-y", "srv"], "env": {"KEY": SECRET}}
    assert call(srv + "/api/connectors", body) == (200, {"name": "web"})
    assert mcp.servers()["web"]["env"] == {"KEY": SECRET}
    st, got = call(srv + "/api/connectors")
    assert st == 200
    assert got["connectors"] == [{"name": "web", "on": True, "programs": {"claude": True, "codex": False},
                                  "spec": {"command": "npx", "args": ["-y", "srv"], "env": {"KEY": "••••42"}}}]
    # editing with the env value left blank keeps it
    assert call(srv + "/api/connectors", {**body, "args": ["srv2"], "env": {"KEY": ""}})[0] == 200
    assert mcp.servers()["web"] == {"command": "npx", "args": ["srv2"], "env": {"KEY": SECRET}}

    assert call(srv + "/api/connectors/web/off", {})[0] == 200
    assert "web" in json.loads(paths.config("mcp-off").read_text())["mcpServers"]
    assert mcp.claude_config() is None and mcp.codex_config() == []
    row = call(srv + "/api/connectors")[1]["connectors"][0]
    assert row["on"] is False and row["programs"] == {"claude": False, "codex": False}
    assert call(srv + "/api/connectors/web/on", {})[0] == 200
    assert "web" in mcp.servers() and mcp.claude_config()
    assert call(srv + "/api/connectors/ghost/on", {})[0] == 404
    assert call(srv + "/api/connectors/web/remove", {})[0] == 200
    assert mcp.all_servers() == []
    assert call(srv + "/api/connectors/web/remove", {})[0] == 404
    assert call(srv + "/api/connectors", {"name": "x"})[0] == 400        # no command, no url


def test_connector_by_url(srv):
    assert call(srv + "/api/connectors", {"name": "remote", "url": "https://r/mcp"})[0] == 200
    assert call(srv + "/api/connectors")[1]["connectors"][0]["spec"] == {"type": "http", "url": "https://r/mcp"}


def test_discover_install_brave(srv):
    st, got = call(srv + "/api/connectors/discover")
    brave = next(c for c in got["catalog"] if c["id"] == "brave-search")
    assert st == 200 and brave["needs"] == ["BRAVE_API_KEY"] and brave["added"] is False
    assert "spec" not in brave
    assert call(srv + "/api/connectors/discover/brave-search", {"env": {}})[0] == 400   # needs the key
    st, done = call(srv + "/api/connectors/discover/brave-search", {"env": {"BRAVE_API_KEY": SECRET}})
    assert (st, done) == (200, {"name": "brave-search", "added": True})
    assert mcp.servers()["brave-search"] == {"command": "npx",
                                             "args": ["-y", "@modelcontextprotocol/server-brave-search"],
                                             "env": {"BRAVE_API_KEY": SECRET}}
    brave = next(c for c in call(srv + "/api/connectors/discover")[1]["catalog"] if c["id"] == "brave-search")
    assert brave["added"] is True
    assert call(srv + "/api/connectors/discover/nope", {})[0] == 404
    assert call(srv + "/api/connectors/discover/filesystem", {})[0] == 400              # needs a folder
    assert call(srv + "/api/connectors/discover/filesystem", {"folder": "/tmp/x"})[0] == 200
    assert mcp.servers()["filesystem"]["args"][-1] == "/tmp/x"
